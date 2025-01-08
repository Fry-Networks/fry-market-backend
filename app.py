from flask import Flask, request, jsonify
from flask_cors import CORS
import base64
import io
import requests
import boto3
import openai
import json
from dotenv import load_dotenv
import os
import pymongo
from bson.objectid import ObjectId
import datetime
import secrets
import jwt
from web3 import Web3
from functools import wraps
from flask_socketio import SocketIO, emit
from flask import Flask, render_template
from flask_socketio import SocketIO, send
import uuid
import json
import random
import string
import re
from botocore.exceptions import ClientError


load_dotenv()

app = Flask(__name__)
CORS(app)

# CORS(app)  # Enable CORS for all routes


# Configuration
engine_id = "stable-diffusion-v1-6"
api_host = os.getenv('API_HOST', 'https://api.stability.ai')
api_key = os.getenv('STABILITY_API_KEY')
s3_bucket = os.getenv('S3_BUCKET')
s3_folder = os.getenv('S3_FOLDER')
openai_api_key = os.getenv('OPENAI_API_KEY')
app.config['SECRET_KEY'] = secrets.token_hex(16)  


# MongoDB connection
client = pymongo.MongoClient("mongodb+srv://frysamuel:WY8umbCtmkr@frynetwork.l921m.mongodb.net/?retryWrites=true&w=majority&appName=frynetwork")
db = client['Frynetwork']
nft_collection = db['nft_collection']
profile_settings_collection = db['profile_settings']
email_collection = db['email']  # Create/Use the 'email' collection
image_collection = db['images']  # Create/Use the 'images' collection

s3 = boto3.client(
    's3',
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
    region_name='us-east-2'
)

# OpenAI setup
openai.api_key = openai_api_key
# socketio = SocketIO(app, cors_allowed_origins="*")
socketio = SocketIO(app)

app.config['SECRET_KEY'] = secrets.token_hex(32)  # Generate a 64-character random hex string for the secret key

def validate_wallet_address(wallet_address):
    # Check if the address length is 64 characters and consists only of alphanumeric characters
    if len(wallet_address) == 58 and wallet_address.isalnum():
        return True
    return False


def generate_token(wallet_address):
    payload = {
        'wallet_address': wallet_address,
        'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=1)  # Token valid for 1 hour
    }
    token = jwt.encode(payload, app.config['SECRET_KEY'], algorithm='HS256')
    
    # Ensure the token is a string
    if isinstance(token, bytes):
        token = token.decode('utf-8')
    
    return token

@app.route('/get-token', methods=['POST'])
def get_token():
    data = request.get_json()
    
    # Debugging: Print the received data
    print("Received data:", data)
    
    wallet_address = data.get('wallet_address')
    
    if not wallet_address:
        return jsonify({'error': 'Wallet address is required'}), 400
    
    # Debugging: Print the wallet address and validation result
    print("Wallet address:", wallet_address)
    if validate_wallet_address(wallet_address):
        token = generate_token(wallet_address)
        return jsonify({'token': token}), 200
    else:
        return jsonify({'error': 'Invalid wallet address'}), 400


def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('x-access-token')
        if not token:
            return jsonify({'error': 'Token is missing!'}), 403

        try:
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            current_wallet_address = data['wallet_address']
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token has expired!'}), 403
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Token is invalid!'}), 403
        
        return f(current_wallet_address, *args, **kwargs)
    
    return decorated

# Function to generate a short unique ID
def generate_short_unique_id(length=5):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def upload_to_s3(file, bucket_name, folder_name="AI"):
    s3_client = boto3.client(
        's3',
        aws_access_key_id='REDACTED_ROTATE_ME',
        aws_secret_access_key='/+KDVga0b2gCjnukWeOFiUF01jMZlJH/D0wadnPA'
    )

    # Ensure the folder name is correct
    folder_name = "AI"  # Explicitly reset folder_name to prevent unwanted changes
    unique_id = generate_short_unique_id()  # Generate unique ID
    print(f"Unique ID: {unique_id}")
    print(f"Folder Name: {folder_name}")
    
    # Construct object_name
    object_name = f"{folder_name}/{unique_id}.png"
    print(f"Object Name: {object_name}")

    try:
        if not hasattr(file, "read"):
            raise ValueError("Invalid file object")

        # Upload file to S3 with ContentType set to 'image/png'
        s3_client.upload_fileobj(
            file,
            bucket_name,
            object_name,
            ExtraArgs={"ContentType": "image/png"}  # Fixed ContentType for PNG images
        )

        # Generate the public file URL
        file_url = f"https://{bucket_name}.s3.amazonaws.com/{object_name}"
        return file_url

    except ValueError as ve:
        raise Exception(f"Invalid file: {ve}")
    except Exception as e:
        raise Exception(f"Failed to upload file: {str(e)}")


# Function to generate images from a prompt
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
                "height": 600,
                "width": 600,
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

            # Decode image from base64 and upload to S3
            image_data = base64.b64decode(image["base64"])
            image_url = upload_to_s3(io.BytesIO(image_data), s3_bucket, s3_key)
            image_urls.append(image_url)

    return image_urls

# Function to generate image description using GPT
def generate_image_description(image_url):
    print(image_url)
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

    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2000,
    )

    description = response.choices[0]
    description_content = description.message.content

    # Clean the description content
    cleaned_description = re.sub(r'\\n', '', description_content)
    cleaned_description = re.sub(r'```json', '', cleaned_description)
    cleaned_description = re.sub(r'```', '', cleaned_description)

    try:
        parsed_json = json.loads(cleaned_description)
    except json.JSONDecodeError as e:
        print(f"Error parsing JSON: {e}")
        return None

    formatted_json = json.dumps(parsed_json, indent=2)
    print(formatted_json)  # Optionally print the formatted JSON for verification

    return formatted_json

# Function to upload metadata to S3
def upload_metadata_to_s3(metadata, s3_bucket, s3_key):
    s3_client = boto3.client('s3')
    s3_client.put_object(Bucket=s3_bucket, Key=s3_key, Body=json.dumps(metadata), ContentType='application/json')
    return f"https://fry-market.s3.amazonaws.com/{s3_key}"

# Route to generate images and upload them with metadata
@app.route('/generate-images', methods=['POST'])
def generate_images_route():
    data = request.json
    wallet_address = data.get('wallet_address')
    prompt = data.get('prompt', "A lighthouse on a cliff")
    style = data.get('style', None)
    num_images = data.get('num_images', 1)

    if not wallet_address:
        return jsonify({"error": "Wallet address is required."}), 400

    if not isinstance(num_images, int) or num_images <= 0:
        return jsonify({"error": "Number of images must be a positive integer."}), 400

    try:
        # Generate images and their URLs
        image_urls = generate_images(num_images, prompt, style)
        image_responses = []

        for image_url in image_urls:
            description = generate_image_description(image_url)
            if not description or not isinstance(description, str):
                return jsonify({"error": "Invalid image description generated."}), 400

            cleaned_description = re.sub(r'//|\\n', '', description)

            try:
                description_json = json.loads(cleaned_description)

                metadata = {
                    "image": image_url,
                    "name": description_json.get("name"),
                    "extra": description_json.get("extra", {}),
                    "standard": description_json.get("standard"),
                    "properties": description_json.get("properties", {}),
                    "description": description_json.get("description"),
                    "image_mime_type": description_json.get("image_mime_type"),
                    "extra_properties": description_json.get("extra_properties", {})
                }
            except json.JSONDecodeError:
                metadata = {
                    "image": image_url,
                    "description": cleaned_description
                }

            metadata_s3_key = f"AI/{image_url.split('/')[-1].split('.')[0]}.json"
            metadata_url = upload_metadata_to_s3(metadata, s3_bucket, metadata_s3_key)

            image_responses.append({
                "name": metadata.get("name"),
                "image": image_url,
                "metadata": metadata_url
            })

        # Save or append the results to the database
        existing_record = image_collection.find_one({"wallet_address": wallet_address})

        if existing_record:
            # Append new images and metadata
            image_collection.update_one(
                {"wallet_address": wallet_address},
                {"$push": {"images": {"$each": image_responses}}}
            )
        else:
            # Insert new record for the wallet address
            image_collection.insert_one({
                "wallet_address": wallet_address,
                "images": image_responses
            })

        return jsonify({"image_responses": image_responses})

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
@app.route('/get-images/<wallet_address>', methods=['GET'])
def get_images_by_wallet(wallet_address):
    if not wallet_address:
        return jsonify({"error": "Wallet address is required."}), 400

    try:
        # Fetch the record for the given wallet address
        record = image_collection.find_one({"wallet_address": wallet_address}, {"_id": 0})  # Exclude MongoDB `_id` field

        if not record:
            return jsonify({"error": "No data found for the provided wallet address."}), 404

        return jsonify(record)

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    
@socketio.on('generate-images')
def generate_images_socket(data):
    data = request.json
    wallet_address = data.get('wallet_address')
    prompt = data.get('prompt', "A lighthouse on a cliff")
    style = data.get('style', None)
    num_images = data.get('num_images', 1)

    if not wallet_address:
        return jsonify({"error": "Wallet address is required."}), 400

    if not isinstance(num_images, int) or num_images <= 0:
        return jsonify({"error": "Number of images must be a positive integer."}), 400

    try:
        # Generate images and their URLs
        image_urls = generate_images(num_images, prompt, style)
        image_responses = []

        for image_url in image_urls:
            description = generate_image_description(image_url)
            if not description or not isinstance(description, str):
                return jsonify({"error": "Invalid image description generated."}), 400

            cleaned_description = re.sub(r'//|\\n', '', description)

            try:
                description_json = json.loads(cleaned_description)

                metadata = {
                    "image": image_url,
                    "name": description_json.get("name"),
                    "extra": description_json.get("extra", {}),
                    "standard": description_json.get("standard"),
                    "properties": description_json.get("properties", {}),
                    "description": description_json.get("description"),
                    "image_mime_type": description_json.get("image_mime_type"),
                    "extra_properties": description_json.get("extra_properties", {})
                }
            except json.JSONDecodeError:
                metadata = {
                    "image": image_url,
                    "description": cleaned_description
                }

            metadata_s3_key = f"AI/{image_url.split('/')[-1].split('.')[0]}.json"
            metadata_url = upload_metadata_to_s3(metadata, s3_bucket, metadata_s3_key)

            image_responses.append({
                "name": metadata.get("name"),
                "image": image_url,
                "metadata": metadata_url
            })

        # Save or append the results to the database
        existing_record = image_collection.find_one({"wallet_address": wallet_address})

        if existing_record:
            # Append new images and metadata
            image_collection.update_one(
                {"wallet_address": wallet_address},
                {"$push": {"images": {"$each": image_responses}}}
            )
        else:
            # Insert new record for the wallet address
            image_collection.insert_one({
                "wallet_address": wallet_address,
                "images": image_responses
            })

        socketio.emit({'image_responses': image_responses})

    except Exception as e:
        socketio.emit('error', {'error': str(e)})

@app.route('/upload-metadata', methods=['POST'])
def upload_metadata():
    metadata = request.json

    image_url = metadata.get('image')
    if not image_url:
        return jsonify({'error': 'No image URL provided'}), 400

    # Create the JSON file name by replacing the file extension with .json
    # Get only the last part of the image URL
    image_filename = image_url.rsplit('/', 1)[-1]  # Extract the last part of the URL
    json_file_name = image_filename.rsplit('.', 1)[0] + '.json'  # Replace .png with .json

    # Upload the JSON metadata to S3
    try:
        s3.put_object(
            Bucket=s3_bucket,  # Use the bucket from the environment variable
            Key=json_file_name,  # Just the file name without folder
            Body=json.dumps(metadata),
            ContentType='application/json'
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    # Return the URL of the uploaded JSON file
    json_url = f'https://{s3_bucket}.s3.amazonaws.com/{json_file_name}'
    return jsonify({'url': json_url}), 200


@app.route('/upload-nft-image', methods=['POST'])
def upload_image():
    # Get image from request
    if 'image' not in request.files:
        return jsonify({'error': 'No image provided'}), 400

    image_file = request.files['image']
    
    # Generate a unique 4-digit ID for the image
    unique_id = str(uuid.uuid4().int)[:4]  # Get first 4 digits of a UUID
    
    # Create the image file name using the unique ID and the original file extension
    image_file_name = f"{unique_id}.{image_file.filename.split('.')[-1]}"
    
    # Upload the image to S3
    try:
        s3.put_object(
            Bucket=s3_bucket,
            Key=image_file_name,
            Body=image_file.read(),
            ContentType=image_file.content_type
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    # Return the URL of the uploaded image
    image_url = f'https://{s3_bucket}.s3.amazonaws.com/{image_file_name}'
    return jsonify({'url': image_url}), 200


@app.route('/')
def index():
    return render_template('index.html')

# API to create a new collection
@app.route('/create-collection', methods=['POST'])
# @token_required
def create_collection():
    data = request.json
    # wallet_address = data.get('wallet_address')
    collection_name = data.get('collection_name')
    collection_address = data.get('collection_address')
    listed_nfts = data.get('listed_nfts', [])
    image_url = data.get('image_url', '')
    description = data.get('description', '')
    royalty = data.get('royalty', 0)  # New field for royalty

    # if wallet_address != current_wallet_address:
    #     return jsonify({'error': 'Wallet address does not match the token!'}), 403

    if not collection_name or not collection_address:
        return jsonify({"error": "Collection name and address are required"}), 400

    # Check if the collection address already exists
    existing_collection_address = nft_collection.find_one({'collection_address': collection_address})
    if existing_collection_address:
        return jsonify({"error": "A collection with this address already exists"}), 400

    # Prepare the data to insert
    collection_data = {
        'collection_name': collection_name,
        'collection_address': collection_address,
        'listed_nfts': listed_nfts,
        'image_url': image_url,
        'description': description,
        'royalty': royalty  # Save the royalty value
    }

    result = nft_collection.insert_one(collection_data)
    return jsonify({"message": "Collection created", "collection_id": str(result.inserted_id)}), 201



# API to update an existing collection (add/remove NFTs in listed NFTs)
@app.route('/update-collection/<collection_address>', methods=['PUT'])
def update_collection(collection_address):
    data = request.json
    add_nfts = data.get('add_nfts', [])
    remove_nfts = data.get('remove_nfts', [])

    collection = nft_collection.find_one({'collection_address': collection_address})

    if not collection:
        return jsonify({"error": "Collection not found"}), 404

    listed_nfts = set(collection['listed_nfts'])
    
    # Add NFTs
    listed_nfts.update(add_nfts)

    # Remove NFTs
    for nft in remove_nfts:
        listed_nfts.discard(nft)

    nft_collection.update_one(
        {'collection_address': collection_address},
        {'$set': {'listed_nfts': list(listed_nfts)}}
    )

    return jsonify({"message": "Collection updated"}), 200

# API to get all collections
@app.route('/get-all-collections', methods=['GET'])
def get_all_collections():
    collections = nft_collection.find()
    collection_list = []
    for collection in collections:
        collection['_id'] = str(collection['_id'])
        collection_list.append(collection)
    return jsonify(collection_list), 200

# API to get a single collection by address
@app.route('/get-collection/<collection_address>', methods=['GET'])
def get_collection_by_address(collection_address):
    collection = nft_collection.find_one({'collection_address': collection_address})

    if not collection:
        return jsonify({"error": "Collection not found"}), 404

    collection['_id'] = str(collection['_id'])
    return jsonify(collection), 200

@app.route('/upload-images', methods=['POST'])
def upload_images():
    if 'images' not in request.files:
        return jsonify({"error": "No images provided"}), 400
    
    image_files = request.files.getlist('images')

    if not image_files or any(image_file.filename == '' for image_file in image_files):
        return jsonify({"error": "One or more images are not selected"}), 400

    try:
        image_urls = []

        for image_file in image_files:
            # Generate a unique name for each image file in S3
            image_name = f"manual_nfts/{image_file.filename}"
            
            # Upload the image to S3
            s3.upload_fileobj(image_file, s3_bucket, image_name)
            
            # Construct the image URL
            image_url = f"https://{s3_bucket}.s3.amazonaws.com/{image_name}"
            image_urls.append(image_url)
        
        return jsonify({"image_urls": image_urls}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/update-followers-following', methods=['POST'])
# @token_required
def update_followers_following():
    data = request.json
    wallet_address = data.get('wallet_address')
    new_followers = data.get('followers', [])
    new_following = data.get('following', [])

    # if wallet_address != current_wallet_address:
    #     return jsonify({'error': 'Wallet address does not match the token!'}), 403

    # Check if the wallet address exists in the artist profile collection
    artist_profile = profile_settings_collection.find_one({'wallet_address': wallet_address})

    if not artist_profile:
        return jsonify({"error": "Artist profile not found"}), 404

    # Append new followers and following data to the existing lists
    updated_followers = list(set(artist_profile.get('followers', []) + new_followers))
    updated_following = list(set(artist_profile.get('following', []) + new_following))

    # Update the artist profile in the database
    profile_settings_collection.update_one(
        {'wallet_address': wallet_address},
        {'$set': {'followers': updated_followers, 'following': updated_following}}
    )

    return jsonify({"message": "Artist profile updated successfully", "wallet_address": wallet_address}), 200


@app.route('/profile-settings', methods=['POST', 'PUT'])
# @token_required
def profile_settings():
    data = request.json
    wallet_address = data.get('wallet_address')
    display_name = data.get('display_name')
    bio = data.get('bio')
    email = data.get('email')
    website_link = data.get('website_link')
    twitter = data.get('twitter')
    discord = data.get('discord')
    instagram = data.get('instagram')
    profile_image = data.get('profile_image')  # URL or base64 encoded image
    banner_image = data.get('banner_image')  # URL or base64 encoded image
    
    # if wallet_address != current_wallet_address:
    #     return jsonify({'error': 'Wallet address does not match the token!'}), 403

    # Check if a profile for this wallet address already exists
    existing_profile = profile_settings_collection.find_one({'wallet_address': wallet_address})
    
    profile_data = {
        'wallet_address': wallet_address,
        'display_name': display_name,
        'bio': bio,
        'email': email,
        'website_link': website_link,
        'twitter': twitter,
        'discord': discord,
        'instagram': instagram,
        'profile_image': profile_image,
        'banner_image': banner_image
    }

    if existing_profile:
        # Update existing profile
        profile_settings_collection.update_one({'wallet_address': wallet_address}, {'$set': profile_data})
        return jsonify({"message": "Profile updated successfully"}), 200
    else:
        # Insert new profile
        profile_settings_collection.insert_one(profile_data)
        return jsonify({"message": "Profile created successfully"}), 201


# API to get profile settings by wallet address
@app.route('/get-profile-settings/<wallet_address>', methods=['GET'])
def get_profile_settings(wallet_address):
    profile = profile_settings_collection.find_one({'wallet_address': wallet_address})

    if not profile:
        return jsonify({"error": "Profile not found"}), 404

    # Convert the MongoDB object to JSON serializable format
    profile['_id'] = str(profile['_id'])
    return jsonify(profile), 200

@app.route('/get-artist-profile/<wallet_address>', methods=['GET'])
def get_artist_profile(wallet_address):
    # Retrieve the artist profile from the database using the provided wallet address
    artist_profile = profile_settings_collection.find_one({'wallet_address': wallet_address})

    if not artist_profile:
        return jsonify({"error": "Artist profile not found"}), 404

    # Extract the required fields from the artist profile
    profile_data = {
        'wallet_address': artist_profile.get('wallet_address'),
        'banner_image': artist_profile.get('banner_image'),
        'profile_image': artist_profile.get('profile_image'),
        'display_name': artist_profile.get('display_name'),
        'bio': artist_profile.get('bio'),
        'followers_count': len(artist_profile.get('followers', [])),
        'following_count': len(artist_profile.get('following', [])),
        'social_links': {
            'website': artist_profile.get('social_links', {}).get('website'),
            'twitter': artist_profile.get('social_links', {}).get('twitter'),
            'discord': artist_profile.get('social_links', {}).get('discord'),
            'instagram': artist_profile.get('social_links', {}).get('instagram'),
            'x': artist_profile.get('social_links', {}).get('x')
        }
    }

    return jsonify({"profile": profile_data}), 200

@app.route('/get-artist-followers/<wallet_address>', methods=['GET'])
def get_artist_followers(wallet_address):
    # Retrieve the artist profile from the database using the provided wallet address
    artist_profile = profile_settings_collection.find_one({'wallet_address': wallet_address})

    if not artist_profile:
        return jsonify({"error": "Artist profile not found"}), 404

    # Extract the list of followers
    followers = artist_profile.get('followers', [])

    return jsonify({"wallet_address": wallet_address, "followers": followers}), 200

@app.route('/get-artist-following/<wallet_address>', methods=['GET'])
def get_artist_following(wallet_address):
    # Retrieve the artist profile from the database using the provided wallet address
    artist_profile = profile_settings_collection.find_one({'wallet_address': wallet_address})

    if not artist_profile:
        return jsonify({"error": "Artist profile not found"}), 404

    # Extract the list of following
    following = artist_profile.get('following', [])

    return jsonify({"wallet_address": wallet_address, "following": following}), 200

# API to get all artist profiles
@app.route('/get-all-profiles', methods=['GET'])
def get_all_profiles():
    profiles = profile_settings_collection.find()
    profile_list = []

    for profile in profiles:
        # Convert each profile to a JSON serializable format
        profile['_id'] = str(profile['_id'])
        profile_data = {
            'wallet_address': profile.get('wallet_address'),
            'banner_image': profile.get('banner_image'),
            'profile_image': profile.get('profile_image'),
            'display_name': profile.get('display_name'),
            'bio': profile.get('bio'),
            'followers_count': len(profile.get('followers', [])),
            'following_count': len(profile.get('following', [])),
            'social_links': {
                'website': profile.get('website_link'),
                'twitter': profile.get('twitter'),
                'discord': profile.get('discord'),
                'instagram': profile.get('instagram'),
            }
        }
        profile_list.append(profile_data)

    return jsonify(profile_list), 200

@app.route('/store-email', methods=['POST'])
def store_email():
    data = request.json
    wallet_address = data.get('wallet_address')
    email = data.get('email')

    # Check for required fields
    if not wallet_address or not email:
        return jsonify({"error": "Both wallet_address and email are required"}), 400

    # Check if the email already exists in the database
    existing_email = email_collection.find_one({'email': email})
    if existing_email:
        return jsonify({"error": "Email already exists"}), 409  # 409 Conflict status code

    # Store data in the 'email' collection
    email_data = {
        'wallet_address': wallet_address,
        'email': email
    }

    email_collection.insert_one(email_data)

    return jsonify({"message": "Success"}), 201

if __name__ == '__main__':
    socketio.run(app, debug=True)
