from fastapi import FastAPI, HTTPException, Header, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi_socketio import SocketManager
import base64
import io
import requests
import boto3
import openai
import json
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, HTTPException
import os
import pymongo
from fastapi.responses import FileResponse
from bson.objectid import ObjectId
import datetime
import secrets
import jwt
from web3 import Web3
from functools import wraps
import uuid
import random
import string
import re
from botocore.exceptions import ClientError
from fastapi import File, UploadFile, Form
from typing import List
import time
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates


# Load environment variables
load_dotenv()

# Initialize FastAPI app
app = FastAPI()

# Set up templates folder (equivalent to Flask's templates folder)
templates = Jinja2Templates(directory="templates")

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration
engine_id = "stable-diffusion-v1-6"
api_host = os.getenv('API_HOST', 'https://api.stability.ai')
api_key = os.getenv('STABILITY_API_KEY')
s3_bucket = os.getenv('S3_BUCKET')
s3_folder = os.getenv('S3_FOLDER')
openai_api_key = os.getenv('OPENAI_API_KEY')
app.secret_key = secrets.token_hex(16)

# MongoDB connection
client = pymongo.MongoClient("mongodb+srv://frysamuel:WY8umbCtmkr@frynetwork.l921m.mongodb.net/?retryWrites=true&w=majority&appName=frynetwork")
db = client['Frynetwork']
nft_collection = db['nft_collection']
profile_settings_collection = db['profile_settings']
email_collection = db['email']  # Create/Use the 'email' collection
image_collection = db['images']  # Create/Use the 'images' collection

# S3 setup
s3_client = boto3.client(
        's3',
        aws_access_key_id='REDACTED_ROTATE_ME',
        aws_secret_access_key='/+KDVga0b2gCjnukWeOFiUF01jMZlJH/D0wadnPA'
    )
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
    data = await request.json()

    # Debugging: Print the received data
    print("Received data:", data)

    wallet_address = data.get('wallet_address')

    if not wallet_address:
        raise HTTPException(status_code=400, detail="Wallet address is required")

    # Debugging: Print the wallet address and validation result
    print("Wallet address:", wallet_address)
    if validate_wallet_address(wallet_address):
        token = generate_token(wallet_address)
        return JSONResponse(content={"token": token}, status_code=200)
    else:
        raise HTTPException(status_code=400, detail="Invalid wallet address")

# Utility functions
def token_required(f):
    @wraps(f)
    async def decorated(*args, x_access_token: str = Header(None), **kwargs):
        if not x_access_token:
            raise HTTPException(status_code=403, detail="Token is missing!")

        try:
            data = jwt.decode(x_access_token, app.secret_key, algorithms=['HS256'])
            current_wallet_address = data['wallet_address']
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=403, detail="Token has expired!")
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=403, detail="Token is invalid!")

        return await f(current_wallet_address, *args, **kwargs)

    return decorated

def generate_short_unique_id(length=5):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def upload_to_s3(file, bucket_name, folder_name="AI"):
    s3_client = boto3.client(
        's3',
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
        region_name='us-east-2'
    )

    folder_name = "AI"
    unique_id = generate_short_unique_id()
    print(f"Unique ID: {unique_id}")
    print(f"Folder Name: {folder_name}")

    object_name = f"{folder_name}/{unique_id}.png"
    print(f"Object Name: {object_name}")

    try:
        if not hasattr(file, "read"):
            raise ValueError("Invalid file object")

        s3_client.upload_fileobj(
            file,
            bucket_name,
            object_name,
            ExtraArgs={"ContentType": "image/png"}
        )

        file_url = f"https://{bucket_name}.s3.amazonaws.com/{object_name}"
        return file_url

    except ValueError as ve:
        raise Exception(f"Invalid file: {ve}")
    except Exception as e:
        raise Exception(f"Failed to upload file: {str(e)}")

def generate_images(num_images, text_prompt="A lighthouse on a cliff", style=None):
    images_per_request = 10
    num_requests = (num_images + images_per_request - 1) // images_per_request
    image_urls = []

    full_prompt = f"Generate an image of {text_prompt}, in {style} style" if style else text_prompt

    for request_idx in range(num_requests):
        samples = min(images_per_request, num_images - request_idx * images_per_request)

        response = requests.post(
            f"{api_host}/v1/generation/{engine_id}/text-to-image",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}"
            },
            json={
                "text_prompts": [
                    {
                        "text": full_prompt
                    }
                ],
                "cfg_scale": 7,
                "height": 1024,
                "width": 1024,
                "samples": samples,
                "steps": 30,
            },
        )

        if response.status_code != 200:
            raise Exception("Non-200 response: " + str(response.text))

        data = response.json()

        for i, image in enumerate(data["artifacts"]):
            image_idx = request_idx * images_per_request + i
            image_name = f"image_{image_idx}"
            s3_key = f"AI/{image_name}"

            image_data = base64.b64decode(image["base64"])
            image_url = upload_to_s3(io.BytesIO(image_data), s3_bucket, s3_key)
            image_urls.append(image_url)

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
    print(f"Generating description for image: {image_url}")
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
    print(prompt)

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

        print(f"Description content: {description_content}")

        # Clean the response content
        cleaned_description = re.sub(r'\\n', '', description_content)
        cleaned_description = re.sub(r'```json', '', cleaned_description)
        cleaned_description = re.sub(r'```', '', cleaned_description)

        # Parse the cleaned content to JSON
        parsed_json = json.loads(cleaned_description)
        return parsed_json  # Return the JSON as a dictionary

    except json.JSONDecodeError as e:
        print(f"Error parsing JSON: {e}")
        raise HTTPException(status_code=500, detail="Error parsing JSON response from GPT.")

    except Exception as e:
        print(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail="An unexpected error occurred.")
    
def upload_metadata_to_s3(metadata, s3_bucket, s3_key):
    s3_client = boto3.client('s3')
    s3_client.put_object(Bucket=s3_bucket, Key=s3_key, Body=json.dumps(metadata), ContentType='application/json')
    return f"https://{s3_bucket}.s3.amazonaws.com/{s3_key}"

@app.post('/generate-images')
async def generate_images_route(request: Request):
    data = await request.json()
    wallet_address = data.get('wallet_address')
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
            description = generate_image_description(image_url)

            if not description or not isinstance(description, str):
                raise HTTPException(status_code=400, detail="Invalid image description generated.")

            cleaned_description = re.sub(r'//|\\n', '', description)

            try:
                description_json = json.loads(cleaned_description)

                metadata = {
                    "wallet_address": wallet_address,
                    "image": image_url,
                    "name": description_json.get("name"),
                    "extra": description_json.get("extra", {}),
                    "standard": description_json.get("standard"),
                    "properties": description_json.get("properties", {}),
                    "description": description_json.get("description"),
                    "image_mime_type": description_json.get("image_mime_type"),
                    "extra_properties": description_json.get("extra_properties", {}),
                    "prompt": prompt,
                    "style": style,
                }
            except json.JSONDecodeError:
                metadata = {
                    "wallet_address": wallet_address,
                    "image": image_url,
                    "description": cleaned_description,
                    "prompt": prompt,
                    "style": style,
                }

            metadata_s3_key = f"AI/{image_url.split('/')[-1].split('.')[0]}.json"
            metadata_url = upload_metadata_to_s3(metadata, s3_bucket, metadata_s3_key)

            # Save metadata to MongoDB
            image_collection.insert_one(metadata)

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

from bson import ObjectId

@app.websocket("/ws")
async def websocket_generate_images(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            try:
                # Receive data from WebSocket
                data = await websocket.receive_json()
                print(f"Received data: {data}")

                # Ensure data is a dictionary
                if not isinstance(data, dict):
                    await websocket.send_json({"error": "Invalid data format. Expected a JSON object."})
                    continue

                # Extract fields from the data
                wallet_address = data.get("wallet_address")
                prompt = data.get("prompt", "A lighthouse on a cliff")
                style = data.get("style", None)
                num_images = data.get("num_images", 1)

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
                        print(f"Generated image URLs for batch {batch_index + 1}: {image_urls}")

                        for image_url in image_urls:
                            # Generate metadata for the image
                            description = await generate_image_description(image_url)
                            print(f"Generated description: {description}")

                            # Ensure description is a parsed dictionary
                            if isinstance(description, str):
                                try:
                                    description = json.loads(description)
                                except json.JSONDecodeError as e:
                                    await websocket.send_json({"error": f"Failed to parse JSON: {str(e)}"})
                                    continue

                            if not isinstance(description, dict):
                                await websocket.send_json({"error": "Invalid description format returned"})
                                continue

                            # Create metadata
                            metadata = {
                                "wallet_address": wallet_address,
                                "name": description.get("name"),
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
                            await websocket.send_json({"type": "image", "data": image_url})

                            # Then send the metadata
                            await websocket.send_json({"type": "metadata", "data": metadata})

                    except Exception as e:
                        await websocket.send_json({"error": f"Failed to process batch {batch_index + 1}: {str(e)}"})

                # Once all batches are processed, send a summary message
                await websocket.send_json({"type": "summary", "data": all_metadata})

                # Send an event indicating all data is generated
                await websocket.send_json({"type": "event", "data": "All data generated"})

            except ValueError as e:
                # Handle invalid JSON
                await websocket.send_json({"error": f"Invalid JSON received: {str(e)}"})
                continue

    except Exception as e:
        print(f"WebSocket error: {e}")
        await websocket.close()
    
@app.post("/upload-metadata")
async def upload_metadata(metadata: dict):
    image_url = metadata.get("image")
    if not image_url:
        raise HTTPException(status_code=400, detail="No image URL provided")

    image_filename = image_url.rsplit("/", 1)[-1]
    json_file_name = image_filename.rsplit(".", 1)[0] + ".json"

    try:
        s3_client.put_object(
            Bucket=s3_bucket,
            Key=json_file_name,
            Body=json.dumps(metadata),
            ContentType="application/json"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    json_url = f"https://{s3_bucket}.s3.amazonaws.com/{json_file_name}"
    return {"url": json_url}

@app.post("/upload-nft-image")
async def upload_image(image: UploadFile = File(...)):
    unique_id = str(uuid.uuid4().int)[:4]
    image_file_name = f"{unique_id}.{image.filename.split('.')[-1]}"

    try:
        s3_client.put_object(
            Bucket=s3_bucket,
            Key=image_file_name,
            Body=image.file.read(),
            ContentType=image.content_type
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    image_url = f"https://{s3_bucket}.s3.amazonaws.com/{image_file_name}"
    return {"url": image_url}

@app.post("/create-collection")
async def create_collection(data: dict):
    collection_name = data.get("collection_name")
    collection_address = data.get("collection_address")
    listed_nfts = data.get("listed_nfts", [])
    image_url = data.get("image_url", "")
    description = data.get("description", "")
    royalty = data.get("royalty", 0)

    if not collection_name or not collection_address:
        raise HTTPException(status_code=400, detail="Collection name and address are required")

    existing_collection = nft_collection.find_one({"collection_address": collection_address})
    if existing_collection:
        raise HTTPException(status_code=400, detail="A collection with this address already exists")

    collection_data = {
        "collection_name": collection_name,
        "collection_address": collection_address,
        "listed_nfts": listed_nfts,
        "image_url": image_url,
        "description": description,
        "royalty": royalty
    }

    result = nft_collection.insert_one(collection_data)
    return {"message": "Collection created", "collection_id": str(result.inserted_id)}

@app.put("/update-collection/{collection_address}")
async def update_collection(collection_address: str, data: dict):
    add_nfts = data.get("add_nfts", [])
    remove_nfts = data.get("remove_nfts", [])

    collection = nft_collection.find_one({"collection_address": collection_address})
    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    listed_nfts = set(collection["listed_nfts"])
    listed_nfts.update(add_nfts)
    for nft in remove_nfts:
        listed_nfts.discard(nft)

    nft_collection.update_one(
        {"collection_address": collection_address},
        {"$set": {"listed_nfts": list(listed_nfts)}}
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
def upload_images(images: List[UploadFile] = File(...)):
    if not images:
        raise HTTPException(status_code=400, detail="No images provided")

    try:
        image_urls = []
        for image_file in images:
            image_name = f"manual_nfts/{image_file.filename}"
            s3_client.upload_fileobj(image_file.file, s3_bucket, image_name)
            image_url = f"https://{s3_bucket}.s3.amazonaws.com/{image_name}"
            image_urls.append(image_url)

        return {"image_urls": image_urls}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/update-followers-following")
def update_followers_following(
    wallet_address: str = Form(...),
    followers: List[str] = Form(default=[]),
    following: List[str] = Form(default=[]),
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

    profile_data = {
        "wallet_address": wallet_address,
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
        profile_settings_collection.update_one(
            {"wallet_address": wallet_address}, {"$set": profile_data}
        )
        return {"message": "Profile updated successfully"}
    else:
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
def store_email(wallet_address: str = Form(...), email: str = Form(...)):
    if not wallet_address or not email:
        raise HTTPException(status_code=400, detail="Both wallet_address and email are required")

    existing_email = email_collection.find_one({"email": email})
    if existing_email:
        raise HTTPException(status_code=409, detail="Email already exists")

    email_data = {"wallet_address": wallet_address, "email": email}
    email_collection.insert_one(email_data)
    return JSONResponse(content={"message": "Success"}, status_code=201)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", reload=True, port=8000)
