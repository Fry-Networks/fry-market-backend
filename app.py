from fastapi import FastAPI, HTTPException, Header, Depends, Request, Body, WebSocket, File, UploadFile, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import base64
import io
import requests
import openai
import json
from dotenv import load_dotenv
import os
from pathlib import Path
import pymongo
from bson import ObjectId
from bson.json_util import dumps
import datetime
import secrets
import jwt
import uuid
import random
import string
import re
import shutil
from typing import List, Optional
import time
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Load environment variables
load_dotenv()

# Initialize FastAPI app
app = FastAPI()

# Set up templates folder (equivalent to Flask's templates folder)
templates = Jinja2Templates(directory="templates")

# CORS configuration
# Auth on this API is via the x-access-token header, not cookies, so
# allow_credentials is not needed and must stay False when allow_origins is "*"
# (combining a wildcard origin with credentialed requests defeats CORS entirely).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration
engine_id = "stable-diffusion-v1-6"
api_host = os.getenv('API_HOST', 'https://api.stability.ai')
api_key = os.getenv('STABILITY_API_KEY')
openai_api_key = os.getenv('OPENAI_API_KEY')
mongodb_uri = os.getenv('MONGODB_URI')
app.secret_key = os.getenv('SECRET_KEY', secrets.token_hex(16))

# Local file storage configuration
UPLOAD_DIR = Path(os.getenv('UPLOAD_DIR', './uploads'))
UPLOAD_BASE_URL = os.getenv('UPLOAD_BASE_URL', '')  # e.g. "https://yourdomain.com" for production

# MongoDB connection
client = pymongo.MongoClient(mongodb_uri)  # Use the environment variable
db = client['Frynetwork']
nft_collection = db['nft_collection']
profile_settings_collection = db['profile_settings']
email_collection = db['email']  # Create/Use the 'email' collection
image_collection = db['images']  # Create/Use the 'images' collection

# Additional MongoDB collections for marketplace features
listing_collection = db['listings']       # NFT listings (fixed price / auction)
bid_collection = db['bids']               # Auction bids
transaction_collection = db['transactions']  # Transaction history

# Local storage setup - create upload directories
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
(UPLOAD_DIR / "AI").mkdir(exist_ok=True)
(UPLOAD_DIR / "manual_nfts").mkdir(exist_ok=True)

# Serve uploaded files as static assets
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")

# OpenAI setup
openai.api_key = openai_api_key

# Utility functions
def validate_wallet_address(wallet_address: str) -> bool:
    """Check if the wallet address is valid."""
    if len(wallet_address) == 58 and wallet_address.isalnum():
        return True
    return False

def generate_token(wallet_address: str) -> str:
    """Generate a JWT token for the wallet address."""
    payload = {
        'wallet_address': wallet_address,
        'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=1)  # Token valid for 1 hour
    }
    token = jwt.encode(payload, app.secret_key, algorithm='HS256')

    if isinstance(token, bytes):
        token = token.decode('utf-8')

    return token

# Routes
@app.post("/get-token")
async def get_token(request: Request):
    """Generate a JWT token based on the provided wallet address."""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    wallet_address = data.get('wallet_address')

    if not wallet_address:
        raise HTTPException(status_code=400, detail="Wallet address is required")
    if validate_wallet_address(wallet_address):
        token = generate_token(wallet_address)
        return JSONResponse(content={"token": token}, status_code=200)
    else:
        raise HTTPException(status_code=400, detail="Invalid wallet address")

# Authentication dependency
async def get_current_wallet(x_access_token: str = Header(None)) -> str:
    """FastAPI dependency that validates JWT token and returns the wallet address."""
    if not x_access_token:
        raise HTTPException(status_code=403, detail="Token is missing!")

    try:
        data = jwt.decode(x_access_token, app.secret_key, algorithms=['HS256'])
        return data['wallet_address']
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=403, detail="Token has expired!")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=403, detail="Token is invalid!")

def generate_short_unique_id(length=5):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def save_file(file, folder_name="AI", extension="png"):
    """Save a file to local storage and return its URL."""
    unique_id = generate_short_unique_id()
    object_name = f"{folder_name}/{unique_id}.{extension}"
    file_path = UPLOAD_DIR / object_name

    try:
        if not hasattr(file, "read"):
            raise ValueError("Invalid file object")

        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "wb") as f:
            f.write(file.read())

        file_url = f"{UPLOAD_BASE_URL}/uploads/{object_name}"
        return file_url

    except ValueError as ve:
        raise Exception(f"Invalid file: {ve}")
    except Exception as e:
        raise Exception(f"Failed to save file: {str(e)}")

def generate_images(num_images, text_prompt="A lighthouse on a cliff", style=None):
    image_urls = []
    full_prompt = f"Generate an image of {text_prompt}, in {style} style" if style else text_prompt

    # Calculate number of API calls needed (OpenAI allows max 10 images per request)
    images_per_request = 10
    num_requests = (num_images + images_per_request - 1) // images_per_request

    for request_idx in range(num_requests):
        samples = min(images_per_request, num_images - request_idx * images_per_request)

        try:
            response = requests.post(
                "https://api.openai.com/v1/images/generations",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {openai_api_key}"
                },
                json={
                    "model": "gpt-image-1",
                    "prompt": full_prompt,
                    "n": samples,
                    "size": "1024x1024",
                    "response_format": "b64_json"
                }
            )

            if response.status_code != 200:
                raise Exception("Non-200 response: " + str(response.text))
            data = response.json()
            for i, image in enumerate(data["data"]):
                image_data = base64.b64decode(image["b64_json"])
                image_url = save_file(io.BytesIO(image_data), "AI")
                image_urls.append(image_url)

        except Exception as e:
            logger.error(f"Error generating images: {str(e)}")
            raise

    return image_urls
# Function to generate image description using GPT
async def generate_image_description(image_url: str) -> dict:
    """
    Generates a detailed JSON description for the provided image URL using GPT.

    Args:
        image_url (str): The URL of the image.

    Returns:
        dict: The generated description in a structured JSON format.
    """
    prompt = f"""
        You are required to provide the response in a specific JSON format.
        The fields required are as follows:
        - "name": a unique identifier for the character
        - "extra": an empty dictionary
        - "image": the provided image URL
        - "standard": set to "arc3"
        - "properties": a dictionary containing attributes like "Eyes", "Skin", "Tail", "Mouth", "Eyewear", "Special", "Headgear", and "Background"
        - "description": a brief character description
        - "image_mime_type": a string representing the image MIME type (e.g., "image/png")
        - "extra_properties": an empty dictionary

        Use the following format for your response:
        {{
            "name": "Character Name",
            "extra": {{}},
            "image": "{image_url}",
            "standard": "arc3",
            "properties": {{
                "Eyes": "Glaring",
                "Skin": "Breeze",
                "Tail": "None",
                "Mouth": "None",
                "Eyewear": "None",
                "Special": "None",
                "Headgear": "Leafs",
                "Background": "Softy"
            }},
            "description": "Character description here",
            "image_mime_type": "image/png",
            "extra_properties": {{}}
        }}
    """
    try:
        # Call OpenAI GPT-4 for generating description
        response = openai.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,
        )

        # Extract the description content
        description = response.choices[0]
        description_content = description.message.content

        # Clean the response content
        cleaned_description = re.sub(r'\\n', '', description_content)
        cleaned_description = re.sub(r'```json', '', cleaned_description)
        cleaned_description = re.sub(r'```', '', cleaned_description)

        # Parse the cleaned content to JSON
        parsed_json = json.loads(cleaned_description)
        return parsed_json  # Return the JSON as a dictionary

    except json.JSONDecodeError as e:
        logger.error(f"Error parsing JSON from GPT: {e}")
        raise HTTPException(status_code=500, detail="Error parsing JSON response from GPT.")

    except Exception as e:
        logger.error(f"Unexpected error in generate_image_description: {e}")
        raise HTTPException(status_code=500, detail="An unexpected error occurred.")
    
def save_metadata(metadata, file_key):
    """Save metadata JSON to local storage and return its URL."""
    file_path = UPLOAD_DIR / file_key
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w") as f:
        json.dump(metadata, f)
    return f"{UPLOAD_BASE_URL}/uploads/{file_key}"

@app.post('/generate-images')
async def generate_images_route(request: Request, current_wallet: str = Depends(get_current_wallet)):
    data = await request.json()
    wallet_address = data.get('wallet_address', current_wallet)
    prompt = data.get('prompt', "A lighthouse on a cliff")
    style = data.get('style', None)
    num_images = data.get('num_images', 1)

    if not isinstance(num_images, int) or num_images <= 0:
        raise HTTPException(status_code=400, detail="Number of images must be a positive integer.")

    if not wallet_address:
        raise HTTPException(status_code=400, detail="Wallet address is required.")

    try:
        image_urls = generate_images(num_images, prompt, style)
        image_responses = []

        for image_url in image_urls:
            description = await generate_image_description(image_url)

            if not description or not isinstance(description, dict):
                raise HTTPException(status_code=400, detail="Invalid image description generated.")

            metadata = {
                "wallet_address": wallet_address,
                "image": image_url,
                "name": description.get("name"),
                "extra": description.get("extra", {}),
                "standard": description.get("standard"),
                "properties": description.get("properties", {}),
                "description": description.get("description"),
                "image_mime_type": description.get("image_mime_type"),
                "extra_properties": description.get("extra_properties", {}),
                "prompt": prompt,
                "style": style,
            }

            metadata_key = f"AI/{image_url.split('/')[-1].split('.')[0]}.json"
            metadata_url = save_metadata(metadata, metadata_key)

            # Save metadata to MongoDB (copy to avoid _id serialization issues)
            metadata_to_store = metadata.copy()
            image_collection.insert_one(metadata_to_store)

            image_responses.append({
                "name": metadata.get("name"),
                "image": image_url,
                "metadata": metadata_url
            })

        return {"image_responses": image_responses}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Define route for the index page
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.websocket("/ws")
async def websocket_generate_images(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            try:
                # Receive data from WebSocket
                data = await websocket.receive_json()

                # Ensure data is a dictionary
                if not isinstance(data, dict):
                    await websocket.send_json({"error": "Invalid data format. Expected a JSON object."})
                    continue

                # Extract fields from the data
                wallet_address = data.get("wallet_address")
                prompt = data.get("prompt", "A lighthouse on a cliff")
                style = data.get("style", None)
                num_images = data.get("num_images", 1)
                collection_name = data.get("collection_name", "Default Collection")  # Get collection name

                # Validate wallet address
                if not wallet_address:
                    await websocket.send_json({"error": "Wallet address is required"})
                    continue

                # Validate number of images
                if not isinstance(num_images, int) or num_images <= 0:
                    await websocket.send_json({"error": "Number of images must be a positive integer"})
                    continue

                # Process images in chunks
                batch_size = 5
                total_batches = (num_images + batch_size - 1) // batch_size
                all_metadata = []

                for batch_index in range(total_batches):
                    batch_start = batch_index * batch_size
                    batch_count = min(batch_size, num_images - batch_start)

                    try:
                        # Generate image URLs
                        image_urls = generate_images(batch_count, prompt, style)

                        for idx, image_url in enumerate(image_urls):
                            try:
                                # Generate metadata for the image
                                description = await generate_image_description(image_url)

                                # Ensure description is a parsed dictionary
                                if isinstance(description, str):
                                    try:
                                        description = json.loads(description)
                                    except json.JSONDecodeError as e:
                                        await websocket.send_json(
                                            {"error": f"Failed to parse JSON description: {str(e)}"}
                                        )
                                        continue

                                if not isinstance(description, dict):
                                    await websocket.send_json({"error": "Invalid description format returned"})
                                    continue

                                # Create image name based on collection name and index
                                image_index = batch_start + idx + 1
                                image_name = f"{collection_name} #{image_index}"

                                # Create metadata with collection-based naming
                                metadata = {
                                    "wallet_address": wallet_address,
                                    "name": image_name,  # Use collection-based name
                                    "collection_name": collection_name,  # Add collection name field
                                    "image": image_url,
                                    "extra": description.get("extra", {}),
                                    "standard": description.get("standard"),
                                    "properties": description.get("properties", {}),
                                    "description": description.get("description"),
                                    "image_mime_type": description.get("image_mime_type"),
                                    "extra_properties": description.get("extra_properties", {}),
                                    "prompt": prompt,
                                    "style": style,
                                }

                                # Save metadata to MongoDB
                                result = image_collection.insert_one(metadata)
                                metadata["_id"] = str(result.inserted_id)  # Convert ObjectId to string
                                all_metadata.append(metadata)

                                # Send the image URL first
                                await websocket.send_text(json.dumps({"type": "image", "data": image_url}))

                                # Then send the metadata
                                await websocket.send_text(json.dumps({"type": "metadata", "data": metadata}))

                            except Exception as e:
                                error_message = f"Failed to process image: {str(e)}"
                                logger.error(error_message)
                                await websocket.send_text(json.dumps({"error": error_message}))

                    except Exception as e:
                        # Handle specific moderation error
                        if "content moderation" in str(e).lower():
                            error_message = f"Batch {batch_index + 1} flagged by content moderation: {str(e)}"
                        else:
                            error_message = f"Failed to process batch {batch_index + 1}: {str(e)}"
                        logger.error(error_message)
                        await websocket.send_text(json.dumps({"error": error_message}))

                # Once all batches are processed, send a summary message
                await websocket.send_text(json.dumps({"type": "summary", "data": all_metadata}))

                # Send an event indicating all data is generated
                await websocket.send_text(json.dumps({"type": "event", "data": "All data generated"}))

            except ValueError as e:
                # Handle invalid JSON
                await websocket.send_text(json.dumps({"error": f"Invalid JSON received: {str(e)}"}))
                continue

    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await websocket.close()


@app.post("/upload-metadata")
async def upload_metadata(metadata: dict, current_wallet: str = Depends(get_current_wallet)):
    image_url = metadata.get("image")
    if not image_url:
        raise HTTPException(status_code=400, detail="No image URL provided")

    image_filename = image_url.rsplit("/", 1)[-1]
    json_file_name = image_filename.rsplit(".", 1)[0] + ".json"

    try:
        file_path = UPLOAD_DIR / json_file_name
        with open(file_path, "w") as f:
            json.dump(metadata, f)
    except Exception as e:
        logger.error(f"Metadata save failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to upload metadata")

    json_url = f"{UPLOAD_BASE_URL}/uploads/{json_file_name}"
    return {"url": json_url}

@app.post("/upload-nft-image")
async def upload_image(image: UploadFile = File(...), current_wallet: str = Depends(get_current_wallet)):
    unique_id = str(uuid.uuid4().int)[:4]
    ext = image.filename.rsplit('.', 1)[-1] if '.' in (image.filename or '') else 'png'
    allowed_extensions = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'}
    if ext.lower() not in allowed_extensions:
        raise HTTPException(status_code=400, detail=f"File type '.{ext}' not allowed")
    image_file_name = f"{unique_id}.{ext}"

    try:
        file_path = UPLOAD_DIR / image_file_name
        with open(file_path, "wb") as f:
            f.write(image.file.read())
    except Exception as e:
        logger.error(f"File save failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to upload image")

    image_url = f"{UPLOAD_BASE_URL}/uploads/{image_file_name}"
    return {"url": image_url}
    
@app.post("/create-collection")
async def create_collection(data: dict, current_wallet: str = Depends(get_current_wallet)):
    # Extract data from the incoming request
    collection_name = data.get("collection_name")
    collection_address = data.get("collection_address")
    wallet_address = data.get("wallet_address")
    image_url = data.get("image_url", "")
    description = data.get("description", "")
    royalty = data.get("royalty", 0.0)
    category = data.get("category", "")

    # Validate required fields
    if not collection_name or not collection_address or not wallet_address:
        raise HTTPException(status_code=400, detail="Collection name, collection address, and wallet address are required")

    # Check if a collection with the same collection_address already exists for the same wallet_address
    existing_collection = nft_collection.find_one({
        "wallet_address": wallet_address,
        "collection_address": collection_address
    })

    if existing_collection:
        raise HTTPException(status_code=400, detail="A collection with this address already exists for this wallet address")

    # Prepare collection data to insert into the database
    collection_data = {
        "collection_name": collection_name,
        "collection_address": collection_address,
        "wallet_address": wallet_address,
        "image_url": image_url,
        "description": description,
        "royalty": royalty,
        "category": category,
        "minted_nfts": [],
        "listed_nfts": [],
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }

    # Insert collection data into the database
    result = nft_collection.insert_one(collection_data)
    return {"message": "Collection created", "collection_id": str(result.inserted_id)}

# Get all collections for a specific wallet address API
@app.get("/get-collections/{wallet_address}")
async def get_collections(wallet_address: str):
    # Fetch collections from the database associated with the given wallet_address
    collections = list(nft_collection.find({"wallet_address": wallet_address}))

    if not collections:
        raise HTTPException(status_code=404, detail="No collections found for this wallet address")

    # Convert ObjectId to string for serialization
    for collection in collections:
        collection['_id'] = str(collection['_id'])

    return {"wallet_address": wallet_address, "collections": collections}

@app.put("/update-collection/{collection_address}")
async def update_collection(collection_address: str, data: dict, current_wallet: str = Depends(get_current_wallet)):
    add_nfts = data.get("add_nfts", [])
    remove_nfts = data.get("remove_nfts", [])
    update_fields = {}

    # Allow updating collection metadata fields
    for field in ["collection_name", "description", "image_url", "royalty", "category"]:
        if field in data:
            update_fields[field] = data[field]

    collection = nft_collection.find_one({"collection_address": collection_address})
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    listed_nfts = set(collection.get("listed_nfts", []))
    listed_nfts.update(add_nfts)
    for nft in remove_nfts:
        listed_nfts.discard(nft)

    update_fields["listed_nfts"] = list(listed_nfts)

    nft_collection.update_one(
        {"collection_address": collection_address},
        {"$set": update_fields}
    )

    return {"message": "Collection updated"}

def serialize_document(document):
    document["_id"] = str(document["_id"])
    return document

@app.get("/get-all-collections")
def get_all_collections():
    collections = list(nft_collection.find())
    collection_list = [serialize_document(collection) for collection in collections]
    return collection_list

@app.get("/get-collection/{collection_address}")
def get_collection_by_address(collection_address: str):
    collection = nft_collection.find_one({"collection_address": collection_address})
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    return serialize_document(collection)

@app.post("/upload-images")
def upload_images(images: List[UploadFile] = File(...), current_wallet: str = Depends(get_current_wallet)):
    if not images:
        raise HTTPException(status_code=400, detail="No images provided")

    try:
        image_urls = []
        for image_file in images:
            relative_path = f"manual_nfts/{image_file.filename}"
            file_path = UPLOAD_DIR / relative_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, "wb") as f:
                shutil.copyfileobj(image_file.file, f)
            image_url = f"{UPLOAD_BASE_URL}/uploads/{relative_path}"
            image_urls.append(image_url)

        return {"image_urls": image_urls}
    except Exception as e:
        logger.error(f"File upload failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to upload images")

@app.post("/update-followers-following")
def update_followers_following(
    wallet_address: str = Form(...),
    followers: List[str] = Form(default=[]),
    following: List[str] = Form(default=[]),
    current_wallet: str = Depends(get_current_wallet),
):
    artist_profile = profile_settings_collection.find_one({"wallet_address": wallet_address})
    if not artist_profile:
        raise HTTPException(status_code=404, detail="Artist profile not found")

    updated_followers = list(set(artist_profile.get("followers", []) + followers))
    updated_following = list(set(artist_profile.get("following", []) + following))

    profile_settings_collection.update_one(
        {"wallet_address": wallet_address},
        {"$set": {"followers": updated_followers, "following": updated_following}},
    )
    return {"message": "Artist profile updated successfully", "wallet_address": wallet_address}

@app.post("/profile-settings")
@app.put("/profile-settings")
def profile_settings(
    current_wallet: str = Depends(get_current_wallet),
    wallet_address: str = Form(...),
    display_name: str = Form(None),
    bio: str = Form(None),
    email: str = Form(None),
    website_link: str = Form(None),
    twitter: str = Form(None),
    discord: str = Form(None),
    instagram: str = Form(None),
    profile_image: str = Form(None),
    banner_image: str = Form(None),
):
    existing_profile = profile_settings_collection.find_one({"wallet_address": wallet_address})

    # Build profile data - only include fields that are provided (not None)
    all_fields = {
        "display_name": display_name,
        "bio": bio,
        "email": email,
        "website_link": website_link,
        "twitter": twitter,
        "discord": discord,
        "instagram": instagram,
        "profile_image": profile_image,
        "banner_image": banner_image,
    }

    if existing_profile:
        # Only update fields that were actually provided
        update_data = {k: v for k, v in all_fields.items() if v is not None}
        if update_data:
            profile_settings_collection.update_one(
                {"wallet_address": wallet_address}, {"$set": update_data}
            )
        return {"message": "Profile updated successfully"}
    else:
        profile_data = {"wallet_address": wallet_address}
        profile_data.update(all_fields)
        # Initialize followers/following arrays for new profiles
        profile_data["followers"] = []
        profile_data["following"] = []
        profile_settings_collection.insert_one(profile_data)
        return JSONResponse(content={"message": "Profile created successfully"}, status_code=201)

@app.get("/get-profile-settings/{wallet_address}")
def get_profile_settings(wallet_address: str):
    profile = profile_settings_collection.find_one({"wallet_address": wallet_address})
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return serialize_document(profile)

@app.get("/get-artist-profile/{wallet_address}")
def get_artist_profile(wallet_address: str):
    artist_profile = profile_settings_collection.find_one({"wallet_address": wallet_address})
    if not artist_profile:
        raise HTTPException(status_code=404, detail="Artist profile not found")

    profile_data = {
        "wallet_address": artist_profile.get("wallet_address"),
        "banner_image": artist_profile.get("banner_image"),
        "profile_image": artist_profile.get("profile_image"),
        "display_name": artist_profile.get("display_name"),
        "bio": artist_profile.get("bio"),
        "followers_count": len(artist_profile.get("followers", [])),
        "following_count": len(artist_profile.get("following", [])),
        "social_links": {
            "website": artist_profile.get("website_link"),
            "twitter": artist_profile.get("twitter"),
            "discord": artist_profile.get("discord"),
            "instagram": artist_profile.get("instagram"),
        },
    }
    return {"profile": profile_data}

@app.get("/get-artist-followers/{wallet_address}")
def get_artist_followers(wallet_address: str):
    artist_profile = profile_settings_collection.find_one({"wallet_address": wallet_address})
    if not artist_profile:
        raise HTTPException(status_code=404, detail="Artist profile not found")
    followers = artist_profile.get("followers", [])
    return {"wallet_address": wallet_address, "followers": followers}

@app.get("/get-artist-following/{wallet_address}")
def get_artist_following(wallet_address: str):
    artist_profile = profile_settings_collection.find_one({"wallet_address": wallet_address})
    if not artist_profile:
        raise HTTPException(status_code=404, detail="Artist profile not found")
    following = artist_profile.get("following", [])
    return {"wallet_address": wallet_address, "following": following}

@app.get("/get-all-profiles")
def get_all_profiles():
    profiles = list(profile_settings_collection.find())
    profile_list = [
        {
            "wallet_address": profile.get("wallet_address"),
            "banner_image": profile.get("banner_image"),
            "profile_image": profile.get("profile_image"),
            "display_name": profile.get("display_name"),
            "bio": profile.get("bio"),
            "followers_count": len(profile.get("followers", [])),
            "following_count": len(profile.get("following", [])),
            "social_links": {
                "website": profile.get("website_link"),
                "twitter": profile.get("twitter"),
                "discord": profile.get("discord"),
                "instagram": profile.get("instagram"),
            },
        }
        for profile in profiles
    ]
    return profile_list

@app.post("/store-email")
def store_email(
    email: str = Form(...),
    wallet_address: Optional[str] = Form(None)
):
    if not email:
        raise HTTPException(status_code=400, detail="email is required")

    if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        raise HTTPException(status_code=400, detail="Invalid email format")

    existing_email = email_collection.find_one({"email": email})
    if existing_email:
        raise HTTPException(status_code=409, detail="Email already exists")

    email_data = {"email": email}
    if wallet_address:
        email_data["wallet_address"] = wallet_address

    email_collection.insert_one(email_data)
    return JSONResponse(content={"message": "Success"}, status_code=201)

# Ehtisham Code Start

@app.put("/update-collection-nft/{collection_address}")
async def update_collection_nft(collection_address: str, payload: dict = Body(...), current_wallet: str = Depends(get_current_wallet)):
    add_nfts = payload.get("add_nfts", [])
    
    # Convert collection_address to ObjectId
    try:
        object_id = ObjectId(collection_address)
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid collection ID")
    
    collection = nft_collection.find_one({"_id": object_id})
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    
    # Convert each nft id in add_nfts to ObjectId and validate format
    converted_ids = []
    for nft_id in add_nfts:
        try:
            converted_ids.append(ObjectId(nft_id))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid NFT id: {nft_id}")
    
    # Check if all provided NFT IDs exist in the images collection
    # existing_images = list(image_collection.find({"_id": {"$in": converted_ids}}))
    # if len(existing_images) != len(converted_ids):
    #     found_ids = {str(image["_id"]) for image in existing_images}
    #     missing_ids = [nft_id for nft_id in add_nfts if nft_id not in found_ids]
    #     raise HTTPException(status_code=404, detail={"error": "Some NFTs not found in images collection", "missing": missing_ids})
    
    # Get the current list of minted NFTs from the collection (if any)
    listed_nfts = set(collection.get("minted_nfts", []))
    
    # Add the new NFT IDs (as strings)
    listed_nfts.update(add_nfts)
    
    # Update the collection with the new list
    nft_collection.update_one(
        {"_id": object_id},
        {"$set": {"minted_nfts": list(listed_nfts)}}
    )
    
    return JSONResponse(content={"message": "Collection updated"}, status_code=200)
    
    

@app.get("/collection-nfts/{collection_id}")
async def get_collection_nfts(collection_id: str):
    # Convert the collection_id parameter to an ObjectId
    try:
        object_id = ObjectId(collection_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid collection ID")
    
    # Find the collection by its _id
    collection = nft_collection.find_one({"_id": object_id})
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")
    
    # Get the list of minted NFT IDs from the collection
    minted_nfts = collection.get("minted_nfts", [])
    
    # Convert each NFT id string to an ObjectId
    try:
        nft_ids = [ObjectId(nft_id) for nft_id in minted_nfts]
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid NFT IDs in collection")
    
    # Query the images collection for documents whose _id is in nft_ids
    nft_data = list(image_collection.find({"_id": {"$in": nft_ids}}))
    
    # Return the data as JSON using dumps to handle ObjectIds properly
    return JSONResponse(content=json.loads(dumps(nft_data)), media_type="application/json", status_code=200)

@app.get("/collection-details-by-nft/{nft_id}")
async def get_collection_details_by_nft(nft_id: str):
    # Find the collection document where minted_nfts contains the given nft_id
    # Try both string and ObjectId formats for compatibility
    collection = nft_collection.find_one({"minted_nfts": nft_id})

    if not collection:
        # Fallback: try as ObjectId
        try:
            collection = nft_collection.find_one({"minted_nfts": ObjectId(nft_id)})
        except Exception:
            pass

    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    serialized = dumps(collection)
    return JSONResponse(
        content=json.loads(serialized),
        status_code=200,
    )



# ============================================================
# NFT Marketplace Endpoints (per Tech Docs Sections 3.1-3.3)
# ============================================================

# --- NFT Listing (Fixed Price & Auction) - Tech Docs Section 3.2 ---

@app.post("/list-nft")
async def list_nft(data: dict, current_wallet: str = Depends(get_current_wallet)):
    """List an NFT for sale (fixed price or auction)."""
    nft_id = data.get("nft_id")
    listing_type = data.get("listing_type", "fixed")  # "fixed" or "auction"
    price = data.get("price")
    currency = data.get("currency", "FRY")
    duration_hours = data.get("duration_hours", 72)  # Default 72 hours for auctions
    reserve_price = data.get("reserve_price")
    category = data.get("category", "")

    if not nft_id or price is None:
        raise HTTPException(status_code=400, detail="nft_id and price are required")

    if not isinstance(price, (int, float)):
        raise HTTPException(status_code=400, detail="price must be a number")

    if listing_type not in ("fixed", "auction"):
        raise HTTPException(status_code=400, detail="listing_type must be 'fixed' or 'auction'")

    if price <= 0:
        raise HTTPException(status_code=400, detail="Price must be greater than 0")

    # Verify the NFT exists
    try:
        nft = image_collection.find_one({"_id": ObjectId(nft_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid NFT ID format")

    if not nft:
        raise HTTPException(status_code=404, detail="NFT not found")

    # Check the NFT belongs to the current wallet
    if nft.get("wallet_address") != current_wallet:
        raise HTTPException(status_code=403, detail="You can only list your own NFTs")

    # Check if already listed
    existing_listing = listing_collection.find_one({
        "nft_id": nft_id,
        "status": {"$in": ["active", "pending"]}
    })
    if existing_listing:
        raise HTTPException(status_code=400, detail="NFT is already listed")

    now = datetime.datetime.now(datetime.timezone.utc)
    listing_data = {
        "nft_id": nft_id,
        "seller_wallet": current_wallet,
        "listing_type": listing_type,
        "price": price,
        "currency": currency,
        "category": category,
        "status": "active",
        "created_at": now.isoformat(),
    }

    if listing_type == "auction":
        end_time = now + datetime.timedelta(hours=duration_hours)
        listing_data["end_time"] = end_time.isoformat()
        listing_data["highest_bid"] = 0
        listing_data["highest_bidder"] = None
        if reserve_price is not None:
            listing_data["reserve_price"] = reserve_price

    result = listing_collection.insert_one(listing_data)
    return {"message": "NFT listed successfully", "listing_id": str(result.inserted_id)}


@app.get("/get-listing/{listing_id}")
async def get_listing(listing_id: str):
    """Get details of a specific listing."""
    try:
        listing = listing_collection.find_one({"_id": ObjectId(listing_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid listing ID")

    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    return JSONResponse(content=json.loads(dumps(listing)), status_code=200)


@app.get("/get-active-listings")
async def get_active_listings(
    listing_type: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    seller: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """Get all active NFT listings with optional filtering."""
    query = {"status": "active"}
    if listing_type:
        query["listing_type"] = listing_type
    if category:
        query["category"] = category
    if seller:
        query["seller_wallet"] = seller

    skip = (page - 1) * limit
    total = listing_collection.count_documents(query)
    listings = list(listing_collection.find(query).sort("created_at", -1).skip(skip).limit(limit))

    return JSONResponse(content={
        "listings": json.loads(dumps(listings)),
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit
    }, status_code=200)


@app.put("/cancel-listing/{listing_id}")
async def cancel_listing(listing_id: str, current_wallet: str = Depends(get_current_wallet)):
    """Cancel an active listing (seller only)."""
    try:
        listing = listing_collection.find_one({"_id": ObjectId(listing_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid listing ID")

    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    if listing["seller_wallet"] != current_wallet:
        raise HTTPException(status_code=403, detail="Only the seller can cancel this listing")

    if listing["status"] != "active":
        raise HTTPException(status_code=400, detail="Listing is not active")

    listing_collection.update_one(
        {"_id": ObjectId(listing_id)},
        {"$set": {"status": "cancelled", "cancelled_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}}
    )

    return {"message": "Listing cancelled"}


# --- Buy NFT - Tech Docs Section 3.2 ---

@app.post("/buy-nft")
async def buy_nft(data: dict, current_wallet: str = Depends(get_current_wallet)):
    """Purchase an NFT at fixed price."""
    listing_id = data.get("listing_id")

    if not listing_id:
        raise HTTPException(status_code=400, detail="listing_id is required")

    try:
        listing = listing_collection.find_one({"_id": ObjectId(listing_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid listing ID")

    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    if listing["status"] != "active":
        raise HTTPException(status_code=400, detail="Listing is no longer active")

    if listing["listing_type"] != "fixed":
        raise HTTPException(status_code=400, detail="This listing is an auction - use place-bid instead")

    if listing["seller_wallet"] == current_wallet:
        raise HTTPException(status_code=400, detail="You cannot buy your own NFT")

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Update listing status
    listing_collection.update_one(
        {"_id": ObjectId(listing_id)},
        {"$set": {"status": "sold", "buyer_wallet": current_wallet, "sold_at": now}}
    )

    # Transfer NFT ownership in images collection
    image_collection.update_one(
        {"_id": ObjectId(listing["nft_id"])},
        {"$set": {"wallet_address": current_wallet},
         "$push": {"ownership_history": {
             "from": listing["seller_wallet"],
             "to": current_wallet,
             "price": listing["price"],
             "currency": listing["currency"],
             "date": now
         }}}
    )

    # Record transaction
    transaction_data = {
        "type": "purchase",
        "listing_id": listing_id,
        "nft_id": listing["nft_id"],
        "seller_wallet": listing["seller_wallet"],
        "buyer_wallet": current_wallet,
        "price": listing["price"],
        "currency": listing["currency"],
        "timestamp": now,
    }
    transaction_collection.insert_one(transaction_data)

    return {"message": "NFT purchased successfully", "nft_id": listing["nft_id"]}


# --- Auction & Bidding - Tech Docs Section 3.2 ---

@app.post("/place-bid")
async def place_bid(data: dict, current_wallet: str = Depends(get_current_wallet)):
    """Place a bid on an auction listing."""
    listing_id = data.get("listing_id")
    bid_amount = data.get("bid_amount")

    if not listing_id or bid_amount is None:
        raise HTTPException(status_code=400, detail="listing_id and bid_amount are required")

    if not isinstance(bid_amount, (int, float)) or bid_amount <= 0:
        raise HTTPException(status_code=400, detail="bid_amount must be a positive number")

    try:
        listing = listing_collection.find_one({"_id": ObjectId(listing_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid listing ID")

    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    if listing["listing_type"] != "auction":
        raise HTTPException(status_code=400, detail="This listing is not an auction")

    if listing["status"] != "active":
        raise HTTPException(status_code=400, detail="Auction is no longer active")

    if listing["seller_wallet"] == current_wallet:
        raise HTTPException(status_code=400, detail="You cannot bid on your own auction")

    # Check auction hasn't expired
    end_time = datetime.datetime.fromisoformat(listing["end_time"])
    if datetime.datetime.now(datetime.timezone.utc) > end_time:
        raise HTTPException(status_code=400, detail="Auction has ended")

    # Bid must be higher than current highest bid and starting price
    min_bid = max(listing.get("highest_bid", 0), listing["price"])
    if bid_amount <= min_bid:
        raise HTTPException(status_code=400, detail=f"Bid must be higher than {min_bid}")

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Record the bid
    bid_data = {
        "listing_id": listing_id,
        "nft_id": listing["nft_id"],
        "bidder_wallet": current_wallet,
        "bid_amount": bid_amount,
        "timestamp": now,
    }
    bid_collection.insert_one(bid_data)

    # Update listing with highest bid
    listing_collection.update_one(
        {"_id": ObjectId(listing_id)},
        {"$set": {"highest_bid": bid_amount, "highest_bidder": current_wallet}}
    )

    return {"message": "Bid placed successfully", "bid_amount": bid_amount}


@app.get("/get-bids/{listing_id}")
async def get_bids(listing_id: str):
    """Get all bids for a specific auction listing."""
    bids = list(bid_collection.find({"listing_id": listing_id}).sort("bid_amount", -1))
    return JSONResponse(content=json.loads(dumps(bids)), status_code=200)


@app.post("/end-auction/{listing_id}")
async def end_auction(listing_id: str, current_wallet: str = Depends(get_current_wallet)):
    """End an auction and finalize the sale to the highest bidder."""
    try:
        listing = listing_collection.find_one({"_id": ObjectId(listing_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid listing ID")

    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    if listing["seller_wallet"] != current_wallet:
        raise HTTPException(status_code=403, detail="Only the seller can end this auction")

    if listing["listing_type"] != "auction":
        raise HTTPException(status_code=400, detail="This listing is not an auction")

    if listing["status"] != "active":
        raise HTTPException(status_code=400, detail="Auction is no longer active")

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    highest_bidder = listing.get("highest_bidder")
    highest_bid = listing.get("highest_bid", 0)

    # Check reserve price
    reserve_price = listing.get("reserve_price", 0)
    if highest_bid < reserve_price or not highest_bidder:
        listing_collection.update_one(
            {"_id": ObjectId(listing_id)},
            {"$set": {"status": "expired", "ended_at": now}}
        )
        return {"message": "Auction ended with no qualifying bids", "status": "expired"}

    # Transfer NFT to highest bidder
    listing_collection.update_one(
        {"_id": ObjectId(listing_id)},
        {"$set": {"status": "sold", "buyer_wallet": highest_bidder, "sold_at": now}}
    )

    image_collection.update_one(
        {"_id": ObjectId(listing["nft_id"])},
        {"$set": {"wallet_address": highest_bidder},
         "$push": {"ownership_history": {
             "from": listing["seller_wallet"],
             "to": highest_bidder,
             "price": highest_bid,
             "currency": listing.get("currency", "FRY"),
             "date": now,
             "type": "auction"
         }}}
    )

    # Record transaction
    transaction_data = {
        "type": "auction_sale",
        "listing_id": listing_id,
        "nft_id": listing["nft_id"],
        "seller_wallet": listing["seller_wallet"],
        "buyer_wallet": highest_bidder,
        "price": highest_bid,
        "currency": listing.get("currency", "FRY"),
        "timestamp": now,
    }
    transaction_collection.insert_one(transaction_data)

    return {"message": "Auction completed", "winner": highest_bidder, "price": highest_bid}


# --- Search & Filter - Tech Docs Section 3.2 ---

@app.get("/search")
async def search_nfts(
    q: Optional[str] = Query(None, description="Search query for name or description"),
    category: Optional[str] = Query(None),
    collection: Optional[str] = Query(None, description="Collection name"),
    creator: Optional[str] = Query(None, description="Creator wallet address"),
    style: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """Search and filter NFTs by various criteria (Tech Docs Section 3.2)."""
    query = {}

    if q:
        query["$or"] = [
            {"name": {"$regex": q, "$options": "i"}},
            {"description": {"$regex": q, "$options": "i"}},
        ]

    if category:
        # Search by category in the NFT's collection
        matching_collections = list(nft_collection.find(
            {"category": {"$regex": category, "$options": "i"}},
            {"collection_name": 1}
        ))
        collection_names = [c["collection_name"] for c in matching_collections]
        if collection_names:
            query["collection_name"] = {"$in": collection_names}
        else:
            return {"results": [], "total": 0, "page": page, "pages": 0}

    if collection:
        query["collection_name"] = {"$regex": collection, "$options": "i"}

    if creator:
        if not isinstance(creator, str) or not creator.isalnum():
            raise HTTPException(status_code=400, detail="Invalid creator wallet address")
        query["wallet_address"] = creator

    if style:
        query["style"] = {"$regex": style, "$options": "i"}

    skip = (page - 1) * limit
    total = image_collection.count_documents(query)
    results = list(image_collection.find(query).sort("_id", -1).skip(skip).limit(limit))

    return JSONResponse(content={
        "results": json.loads(dumps(results)),
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit
    }, status_code=200)


# --- Transaction History - Tech Docs Section 3.1 / 3.3 ---

@app.get("/transaction-history/{wallet_address}")
async def get_transaction_history(
    wallet_address: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """Get transaction history for a wallet address (Tech Docs Section 3.3)."""
    query = {
        "$or": [
            {"seller_wallet": wallet_address},
            {"buyer_wallet": wallet_address},
        ]
    }

    skip = (page - 1) * limit
    total = transaction_collection.count_documents(query)
    transactions = list(
        transaction_collection.find(query).sort("timestamp", -1).skip(skip).limit(limit)
    )

    return JSONResponse(content={
        "transactions": json.loads(dumps(transactions)),
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit
    }, status_code=200)


# --- NFT Detail View with Ownership History - Tech Docs Section 3.2 ---

@app.get("/nft/{nft_id}")
async def get_nft_detail(nft_id: str):
    """Get detailed NFT information including ownership history and metadata (Tech Docs Section 3.2)."""
    try:
        nft = image_collection.find_one({"_id": ObjectId(nft_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid NFT ID")

    if not nft:
        raise HTTPException(status_code=404, detail="NFT not found")

    nft_data = json.loads(dumps(nft))

    # Get active listing if any
    active_listing = listing_collection.find_one({
        "nft_id": nft_id,
        "status": "active"
    })
    if active_listing:
        nft_data["listing"] = json.loads(dumps(active_listing))

    # Get transaction history for this NFT
    transactions = list(
        transaction_collection.find({"nft_id": nft_id}).sort("timestamp", -1)
    )
    nft_data["transactions"] = json.loads(dumps(transactions))

    # Get the collection this NFT belongs to (try both string and ObjectId)
    collection = nft_collection.find_one({"minted_nfts": nft_id})
    if not collection:
        try:
            collection = nft_collection.find_one({"minted_nfts": ObjectId(nft_id)})
        except Exception:
            pass
    if collection:
        nft_data["collection"] = json.loads(dumps(collection))

    return JSONResponse(content=nft_data, status_code=200)


@app.get("/nfts/{wallet_address}")
async def get_nfts_by_wallet(
    wallet_address: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """Get all NFTs owned by a wallet address."""
    query = {"wallet_address": wallet_address}
    skip = (page - 1) * limit
    total = image_collection.count_documents(query)
    nfts = list(image_collection.find(query).sort("_id", -1).skip(skip).limit(limit))

    return JSONResponse(content={
        "nfts": json.loads(dumps(nfts)),
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit
    }, status_code=200)


# --- Fee Display Endpoint - Tech Docs Section 3.3 ---

@app.get("/fees")
async def get_fees():
    """Get current marketplace fee structure (Tech Docs Section 3.3)."""
    return {
        "listing_fee": 0,
        "transaction_fee_percent": 2.5,
        "collection_generation_fee": 0,
        "currency": "FRY",
        "description": "Transaction fees are applied to successful sales."
    }


if __name__ == "__main__":
    import uvicorn
    debug = os.getenv("DEBUG", "false").lower() == "true"
    uvicorn.run("app:app", reload=debug, port=8000)
