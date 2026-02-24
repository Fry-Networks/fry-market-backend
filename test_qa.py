"""
Comprehensive QA test suite for fry-market-backend.
Uses mocked MongoDB, S3, and OpenAI to test all endpoints.
"""
import os
import sys
import json
import datetime
import io

# Set env vars BEFORE importing app
os.environ["STABILITY_API_KEY"] = "test-key"
os.environ["AWS_ACCESS_KEY_ID"] = "test-access-key"
os.environ["AWS_SECRET_ACCESS_KEY"] = "test-secret-key"
os.environ["S3_BUCKET"] = "test-bucket"
os.environ["S3_FOLDER"] = "test-folder"
os.environ["OPENAI_API_KEY"] = "test-openai-key"
os.environ["MONGODB_URI"] = "mongomock://localhost"
os.environ["SECRET_KEY"] = "test-secret-key-for-jwt"
os.environ["AWS_REGION"] = "us-east-2"

# Patch pymongo to use mongomock BEFORE app import
import mongomock
import unittest.mock as mock

# Patch pymongo.MongoClient with mongomock
mock_client = mongomock.MongoClient()
with mock.patch("pymongo.MongoClient", return_value=mock_client):
    # Patch boto3 S3 client
    mock_s3 = mock.MagicMock()
    mock_s3.upload_fileobj = mock.MagicMock(return_value=None)
    mock_s3.put_object = mock.MagicMock(return_value=None)

    with mock.patch("boto3.client", return_value=mock_s3):
        import app as app_module

from fastapi.testclient import TestClient

client = TestClient(app_module.app)

# Use the mocked DB collections directly
db = mock_client["Frynetwork"]
nft_collection = db["nft_collection"]
profile_settings_collection = db["profile_settings"]
email_collection = db["email"]
image_collection = db["images"]
listing_collection = db["listings"]
bid_collection = db["bids"]
transaction_collection = db["transactions"]

# Also patch the app module's collections to point to our mock collections
app_module.nft_collection = nft_collection
app_module.profile_settings_collection = profile_settings_collection
app_module.email_collection = email_collection
app_module.image_collection = image_collection
app_module.listing_collection = listing_collection
app_module.bid_collection = bid_collection
app_module.transaction_collection = transaction_collection
app_module.s3_client = mock_s3

# ============================================================
# Test Helpers
# ============================================================

ISSUES = []
PASSED = []

VALID_WALLET = "A" * 58  # 58 alphanumeric chars - valid Algorand wallet
VALID_WALLET_2 = "B" * 58
INVALID_WALLET = "short"

def get_token(wallet=VALID_WALLET):
    """Get a JWT token for a wallet address."""
    resp = client.post("/get-token", json={"wallet_address": wallet})
    if resp.status_code == 200:
        return resp.json()["token"]
    return None

def auth_header(wallet=VALID_WALLET):
    """Get auth header dict."""
    token = get_token(wallet)
    return {"x-access-token": token}

def record(test_name, passed, detail=""):
    if passed:
        PASSED.append(test_name)
        print(f"  PASS: {test_name}")
    else:
        ISSUES.append({"test": test_name, "detail": detail})
        print(f"  FAIL: {test_name} -> {detail}")

def cleanup():
    """Clean all mock collections."""
    nft_collection.drop()
    profile_settings_collection.drop()
    email_collection.drop()
    image_collection.drop()
    listing_collection.drop()
    bid_collection.drop()
    transaction_collection.drop()

# ============================================================
# Test 1: Authentication
# ============================================================
def test_authentication():
    print("\n=== TEST: Authentication ===")
    cleanup()

    # Test 1a: Get token with valid wallet
    resp = client.post("/get-token", json={"wallet_address": VALID_WALLET})
    record("get-token valid wallet", resp.status_code == 200, f"status={resp.status_code}, body={resp.text}")

    if resp.status_code == 200:
        token = resp.json().get("token")
        record("get-token returns token", token is not None, f"token={token}")

    # Test 1b: Get token with invalid wallet
    resp = client.post("/get-token", json={"wallet_address": INVALID_WALLET})
    record("get-token invalid wallet returns 400", resp.status_code == 400, f"status={resp.status_code}")

    # Test 1c: Get token with missing wallet
    resp = client.post("/get-token", json={})
    record("get-token missing wallet returns 400", resp.status_code == 400, f"status={resp.status_code}")

    # Test 1d: Access protected endpoint without token
    resp = client.post("/create-collection", json={"collection_name": "test"})
    record("protected endpoint without token returns 403", resp.status_code == 403, f"status={resp.status_code}, body={resp.text}")

    # Test 1e: Access protected endpoint with invalid token
    resp = client.post("/create-collection", json={"collection_name": "test"}, headers={"x-access-token": "invalid-token"})
    record("protected endpoint with invalid token returns 403", resp.status_code == 403, f"status={resp.status_code}, body={resp.text}")

# ============================================================
# Test 2: Profile Endpoints
# ============================================================
def test_profiles():
    print("\n=== TEST: Profile Endpoints ===")
    cleanup()

    headers = auth_header()

    # Test 2a: Create profile
    resp = client.post("/profile-settings", data={
        "wallet_address": VALID_WALLET,
        "display_name": "Test User",
        "bio": "Test bio",
        "email": "test@example.com",
        "twitter": "@testuser",
    }, headers=headers)
    record("create profile", resp.status_code == 201, f"status={resp.status_code}, body={resp.text}")

    # Test 2b: Get profile
    resp = client.get(f"/get-profile-settings/{VALID_WALLET}")
    record("get profile", resp.status_code == 200, f"status={resp.status_code}, body={resp.text}")
    if resp.status_code == 200:
        data = resp.json()
        record("profile has display_name", data.get("display_name") == "Test User", f"display_name={data.get('display_name')}")
        record("profile has bio", data.get("bio") == "Test bio", f"bio={data.get('bio')}")

    # Test 2c: Update profile (only update bio, keep display_name)
    resp = client.put("/profile-settings", data={
        "wallet_address": VALID_WALLET,
        "bio": "Updated bio",
    }, headers=headers)
    record("update profile", resp.status_code == 200, f"status={resp.status_code}, body={resp.text}")

    # Check that display_name wasn't overwritten
    resp = client.get(f"/get-profile-settings/{VALID_WALLET}")
    if resp.status_code == 200:
        data = resp.json()
        record("update preserves display_name", data.get("display_name") == "Test User", f"display_name={data.get('display_name')}")
        record("update changes bio", data.get("bio") == "Updated bio", f"bio={data.get('bio')}")

    # Test 2d: Get non-existent profile
    resp = client.get(f"/get-profile-settings/{VALID_WALLET_2}")
    record("get non-existent profile returns 404", resp.status_code == 404, f"status={resp.status_code}")

    # Test 2e: Get artist profile
    resp = client.get(f"/get-artist-profile/{VALID_WALLET}")
    record("get artist profile", resp.status_code == 200, f"status={resp.status_code}, body={resp.text}")
    if resp.status_code == 200:
        data = resp.json()
        record("artist profile has social_links", "social_links" in data.get("profile", {}), f"keys={data.get('profile', {}).keys()}")

    # Test 2f: Get all profiles
    resp = client.get("/get-all-profiles")
    record("get all profiles", resp.status_code == 200, f"status={resp.status_code}")
    if resp.status_code == 200:
        record("all profiles returns list", isinstance(resp.json(), list), f"type={type(resp.json())}")

    # Test 2g: Update followers/following
    # First create a second profile
    headers2 = auth_header(VALID_WALLET_2)
    resp = client.post("/profile-settings", data={
        "wallet_address": VALID_WALLET_2,
        "display_name": "Test User 2",
    }, headers=headers2)

    resp = client.post("/update-followers-following", data={
        "wallet_address": VALID_WALLET,
        "followers": [VALID_WALLET_2],
    }, headers=headers)
    record("update followers", resp.status_code == 200, f"status={resp.status_code}, body={resp.text}")

    # Test 2h: Get followers
    resp = client.get(f"/get-artist-followers/{VALID_WALLET}")
    record("get followers", resp.status_code == 200, f"status={resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        record("followers list correct", VALID_WALLET_2 in data.get("followers", []), f"followers={data.get('followers')}")

    # Test 2i: Get following
    resp = client.get(f"/get-artist-following/{VALID_WALLET}")
    record("get following", resp.status_code == 200, f"status={resp.status_code}")

# ============================================================
# Test 3: Collection Endpoints
# ============================================================
def test_collections():
    print("\n=== TEST: Collection Endpoints ===")
    cleanup()

    headers = auth_header()

    # Test 3a: Create collection
    resp = client.post("/create-collection", json={
        "collection_name": "Test Collection",
        "collection_address": "col_addr_001",
        "wallet_address": VALID_WALLET,
        "description": "A test collection",
        "royalty": 5.0,
        "category": "Art",
    }, headers=headers)
    record("create collection", resp.status_code == 200, f"status={resp.status_code}, body={resp.text}")

    collection_id = None
    if resp.status_code == 200:
        collection_id = resp.json().get("collection_id")
        record("create collection returns id", collection_id is not None, f"id={collection_id}")

    # Test 3b: Create duplicate collection
    resp = client.post("/create-collection", json={
        "collection_name": "Test Collection",
        "collection_address": "col_addr_001",
        "wallet_address": VALID_WALLET,
    }, headers=headers)
    record("duplicate collection returns 400", resp.status_code == 400, f"status={resp.status_code}")

    # Test 3c: Create collection missing required fields
    resp = client.post("/create-collection", json={
        "collection_name": "Test",
    }, headers=headers)
    record("missing fields returns 400", resp.status_code == 400, f"status={resp.status_code}")

    # Test 3d: Get collections for wallet
    resp = client.get(f"/get-collections/{VALID_WALLET}")
    record("get collections for wallet", resp.status_code == 200, f"status={resp.status_code}, body={resp.text}")
    if resp.status_code == 200:
        data = resp.json()
        record("collections list has items", len(data.get("collections", [])) > 0, f"count={len(data.get('collections', []))}")
        col = data["collections"][0]
        record("collection has minted_nfts", "minted_nfts" in col, f"keys={col.keys()}")
        record("collection has listed_nfts", "listed_nfts" in col, f"keys={col.keys()}")
        record("collection has category", "category" in col, f"keys={col.keys()}")

    # Test 3e: Get all collections
    resp = client.get("/get-all-collections")
    record("get all collections", resp.status_code == 200, f"status={resp.status_code}")

    # Test 3f: Get collection by address
    resp = client.get("/get-collection/col_addr_001")
    record("get collection by address", resp.status_code == 200, f"status={resp.status_code}, body={resp.text}")

    # Test 3g: Get non-existent collection
    resp = client.get("/get-collection/nonexistent")
    record("get non-existent collection returns 404", resp.status_code == 404, f"status={resp.status_code}")

    # Test 3h: Update collection
    resp = client.put("/update-collection/col_addr_001", json={
        "description": "Updated description",
        "category": "Photography",
    }, headers=headers)
    record("update collection", resp.status_code == 200, f"status={resp.status_code}, body={resp.text}")

    # Verify update
    resp = client.get("/get-collection/col_addr_001")
    if resp.status_code == 200:
        data = resp.json()
        record("update collection applied", data.get("description") == "Updated description", f"desc={data.get('description')}")
        record("update collection category", data.get("category") == "Photography", f"cat={data.get('category')}")

    # Test 3i: Update collection NFTs (add minted NFTs)
    # First insert an image NFT
    nft_result = image_collection.insert_one({
        "wallet_address": VALID_WALLET,
        "name": "Test NFT #1",
        "image": "https://test-bucket.s3.amazonaws.com/AI/test.png",
        "description": "A test NFT",
    })
    nft_id = str(nft_result.inserted_id)

    if collection_id:
        resp = client.put(f"/update-collection-nft/{collection_id}", json={
            "add_nfts": [nft_id],
        }, headers=headers)
        record("update collection nft", resp.status_code == 200, f"status={resp.status_code}, body={resp.text}")

        # Test 3j: Get collection NFTs
        resp = client.get(f"/collection-nfts/{collection_id}")
        record("get collection nfts", resp.status_code == 200, f"status={resp.status_code}, body={resp.text}")
        if resp.status_code == 200:
            data = resp.json()
            record("collection nfts has items", len(data) > 0, f"count={len(data)}")

        # Test 3k: Get collection details by NFT
        resp = client.get(f"/collection-details-by-nft/{nft_id}")
        record("get collection by nft", resp.status_code == 200, f"status={resp.status_code}, body={resp.text}")

    return collection_id, nft_id

# ============================================================
# Test 4: NFT Listing Endpoints
# ============================================================
def test_nft_listing():
    print("\n=== TEST: NFT Listing Endpoints ===")
    cleanup()

    headers = auth_header()
    headers2 = auth_header(VALID_WALLET_2)

    # Create an NFT
    nft_result = image_collection.insert_one({
        "wallet_address": VALID_WALLET,
        "name": "Listing Test NFT",
        "image": "https://test-bucket.s3.amazonaws.com/AI/listing.png",
        "description": "NFT for listing test",
        "collection_name": "Test Col",
        "style": "realistic",
    })
    nft_id = str(nft_result.inserted_id)

    # Test 4a: List NFT for fixed price
    resp = client.post("/list-nft", json={
        "nft_id": nft_id,
        "listing_type": "fixed",
        "price": 100,
        "currency": "FRY",
        "category": "Art",
    }, headers=headers)
    record("list nft fixed price", resp.status_code == 200, f"status={resp.status_code}, body={resp.text}")

    listing_id = None
    if resp.status_code == 200:
        listing_id = resp.json().get("listing_id")
        record("list nft returns listing_id", listing_id is not None, f"id={listing_id}")

    # Test 4b: Try to list already-listed NFT
    resp = client.post("/list-nft", json={
        "nft_id": nft_id,
        "listing_type": "fixed",
        "price": 200,
    }, headers=headers)
    record("duplicate listing returns 400", resp.status_code == 400, f"status={resp.status_code}")

    # Test 4c: List NFT not owned by user
    nft_result2 = image_collection.insert_one({
        "wallet_address": VALID_WALLET_2,
        "name": "Other User NFT",
        "image": "https://test.com/other.png",
    })
    resp = client.post("/list-nft", json={
        "nft_id": str(nft_result2.inserted_id),
        "listing_type": "fixed",
        "price": 50,
    }, headers=headers)  # wallet 1 trying to list wallet 2's NFT
    record("list other user nft returns 403", resp.status_code == 403, f"status={resp.status_code}")

    # Test 4d: List with invalid price
    nft_result3 = image_collection.insert_one({
        "wallet_address": VALID_WALLET,
        "name": "Bad Price NFT",
        "image": "https://test.com/bad.png",
    })
    resp = client.post("/list-nft", json={
        "nft_id": str(nft_result3.inserted_id),
        "listing_type": "fixed",
        "price": -10,
    }, headers=headers)
    record("negative price returns 400", resp.status_code == 400, f"status={resp.status_code}")

    # Test 4e: List with missing fields
    resp = client.post("/list-nft", json={}, headers=headers)
    record("missing fields returns 400", resp.status_code == 400, f"status={resp.status_code}")

    # Test 4f: Get listing details
    if listing_id:
        resp = client.get(f"/get-listing/{listing_id}")
        record("get listing", resp.status_code == 200, f"status={resp.status_code}, body={resp.text}")

    # Test 4g: Get active listings
    resp = client.get("/get-active-listings")
    record("get active listings", resp.status_code == 200, f"status={resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        record("active listings has items", data.get("total", 0) > 0, f"total={data.get('total')}")
        record("active listings has pagination", "pages" in data, f"keys={data.keys()}")

    # Test 4h: Filter listings by type
    resp = client.get("/get-active-listings?listing_type=fixed")
    record("filter listings by type", resp.status_code == 200, f"status={resp.status_code}")

    # Test 4i: Filter listings by category
    resp = client.get("/get-active-listings?category=Art")
    record("filter listings by category", resp.status_code == 200, f"status={resp.status_code}")

    # Test 4j: Cancel listing by wrong user
    if listing_id:
        resp = client.put(f"/cancel-listing/{listing_id}", headers=headers2)
        record("cancel listing wrong user returns 403", resp.status_code == 403, f"status={resp.status_code}")

    return listing_id, nft_id

# ============================================================
# Test 5: Buy NFT
# ============================================================
def test_buy_nft():
    print("\n=== TEST: Buy NFT ===")
    cleanup()

    headers = auth_header()
    headers2 = auth_header(VALID_WALLET_2)

    # Create an NFT and list it
    nft_result = image_collection.insert_one({
        "wallet_address": VALID_WALLET,
        "name": "Buy Test NFT",
        "image": "https://test.com/buy.png",
        "description": "NFT for buy test",
    })
    nft_id = str(nft_result.inserted_id)

    resp = client.post("/list-nft", json={
        "nft_id": nft_id,
        "listing_type": "fixed",
        "price": 100,
    }, headers=headers)
    listing_id = resp.json().get("listing_id")

    # Test 5a: Buy own NFT (should fail)
    resp = client.post("/buy-nft", json={"listing_id": listing_id}, headers=headers)
    record("buy own nft returns 400", resp.status_code == 400, f"status={resp.status_code}")

    # Test 5b: Buy NFT as another user
    resp = client.post("/buy-nft", json={"listing_id": listing_id}, headers=headers2)
    record("buy nft success", resp.status_code == 200, f"status={resp.status_code}, body={resp.text}")

    # Verify NFT ownership transferred
    nft = image_collection.find_one({"_id": nft_result.inserted_id})
    record("nft ownership transferred", nft.get("wallet_address") == VALID_WALLET_2, f"owner={nft.get('wallet_address')}")
    record("nft has ownership_history", "ownership_history" in nft, f"keys={nft.keys()}")

    # Verify listing is now sold
    from bson import ObjectId
    listing = listing_collection.find_one({"_id": ObjectId(listing_id)})
    record("listing status is sold", listing.get("status") == "sold", f"status={listing.get('status')}")

    # Verify transaction recorded
    tx = transaction_collection.find_one({"nft_id": nft_id})
    record("transaction recorded", tx is not None, f"tx={tx}")
    if tx:
        record("transaction has correct type", tx.get("type") == "purchase", f"type={tx.get('type')}")

    # Test 5c: Try to buy already sold NFT
    resp = client.post("/buy-nft", json={"listing_id": listing_id}, headers=headers2)
    record("buy sold nft returns 400", resp.status_code == 400, f"status={resp.status_code}")

    # Test 5d: Buy missing listing_id
    resp = client.post("/buy-nft", json={}, headers=headers2)
    record("buy missing listing_id returns 400", resp.status_code == 400, f"status={resp.status_code}")

# ============================================================
# Test 6: Auction & Bidding
# ============================================================
def test_auctions():
    print("\n=== TEST: Auctions & Bidding ===")
    cleanup()

    headers = auth_header()
    headers2 = auth_header(VALID_WALLET_2)

    # Create and list NFT as auction
    nft_result = image_collection.insert_one({
        "wallet_address": VALID_WALLET,
        "name": "Auction Test NFT",
        "image": "https://test.com/auction.png",
    })
    nft_id = str(nft_result.inserted_id)

    resp = client.post("/list-nft", json={
        "nft_id": nft_id,
        "listing_type": "auction",
        "price": 50,
        "duration_hours": 24,
        "reserve_price": 100,
    }, headers=headers)
    record("create auction listing", resp.status_code == 200, f"status={resp.status_code}, body={resp.text}")

    listing_id = resp.json().get("listing_id") if resp.status_code == 200 else None

    if listing_id:
        # Test 6a: Try to buy auction (should fail)
        resp = client.post("/buy-nft", json={"listing_id": listing_id}, headers=headers2)
        record("buy auction returns 400", resp.status_code == 400, f"status={resp.status_code}")

        # Test 6b: Place bid lower than starting price
        resp = client.post("/place-bid", json={
            "listing_id": listing_id,
            "bid_amount": 30,
        }, headers=headers2)
        record("bid below starting price returns 400", resp.status_code == 400, f"status={resp.status_code}")

        # Test 6c: Place valid bid
        resp = client.post("/place-bid", json={
            "listing_id": listing_id,
            "bid_amount": 60,
        }, headers=headers2)
        record("place valid bid", resp.status_code == 200, f"status={resp.status_code}, body={resp.text}")

        # Test 6d: Place higher bid
        resp = client.post("/place-bid", json={
            "listing_id": listing_id,
            "bid_amount": 120,
        }, headers=headers2)
        record("place higher bid", resp.status_code == 200, f"status={resp.status_code}, body={resp.text}")

        # Test 6e: Bid on own auction
        resp = client.post("/place-bid", json={
            "listing_id": listing_id,
            "bid_amount": 200,
        }, headers=headers)
        record("bid on own auction returns 400", resp.status_code == 400, f"status={resp.status_code}")

        # Test 6f: Get bids
        resp = client.get(f"/get-bids/{listing_id}")
        record("get bids", resp.status_code == 200, f"status={resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            record("bids returned", len(data) == 2, f"count={len(data)}")

        # Test 6g: End auction by wrong user
        resp = client.post(f"/end-auction/{listing_id}", headers=headers2)
        record("end auction wrong user returns 403", resp.status_code == 403, f"status={resp.status_code}")

        # Test 6h: End auction by seller (with reserve met)
        resp = client.post(f"/end-auction/{listing_id}", headers=headers)
        record("end auction success", resp.status_code == 200, f"status={resp.status_code}, body={resp.text}")
        if resp.status_code == 200:
            data = resp.json()
            record("auction winner correct", data.get("winner") == VALID_WALLET_2, f"winner={data.get('winner')}")
            record("auction price correct", data.get("price") == 120, f"price={data.get('price')}")

        # Verify ownership
        nft = image_collection.find_one({"_id": nft_result.inserted_id})
        record("auction nft ownership transferred", nft.get("wallet_address") == VALID_WALLET_2, f"owner={nft.get('wallet_address')}")

    # Test 6i: Auction with reserve not met
    nft_result2 = image_collection.insert_one({
        "wallet_address": VALID_WALLET,
        "name": "Reserve Not Met NFT",
        "image": "https://test.com/reserve.png",
    })

    resp = client.post("/list-nft", json={
        "nft_id": str(nft_result2.inserted_id),
        "listing_type": "auction",
        "price": 50,
        "reserve_price": 500,
    }, headers=headers)
    listing_id2 = resp.json().get("listing_id") if resp.status_code == 200 else None

    if listing_id2:
        # Bid below reserve
        resp = client.post("/place-bid", json={
            "listing_id": listing_id2,
            "bid_amount": 60,
        }, headers=headers2)

        resp = client.post(f"/end-auction/{listing_id2}", headers=headers)
        record("auction reserve not met expires", resp.status_code == 200, f"status={resp.status_code}, body={resp.text}")
        if resp.status_code == 200:
            record("auction status expired", resp.json().get("status") == "expired", f"data={resp.json()}")

# ============================================================
# Test 7: Search & Filter
# ============================================================
def test_search():
    print("\n=== TEST: Search & Filter ===")
    cleanup()

    # Create test NFTs
    image_collection.insert_many([
        {"wallet_address": VALID_WALLET, "name": "Golden Dragon", "description": "A golden dragon", "collection_name": "Fantasy", "style": "anime"},
        {"wallet_address": VALID_WALLET, "name": "Silver Phoenix", "description": "A silver phoenix", "collection_name": "Fantasy", "style": "realistic"},
        {"wallet_address": VALID_WALLET_2, "name": "Cyber City", "description": "A cyberpunk city", "collection_name": "SciFi", "style": "digital"},
        {"wallet_address": VALID_WALLET, "name": "Ocean Wave", "description": "Peaceful ocean", "collection_name": "Nature", "style": "watercolor"},
    ])

    # Create collections with categories
    nft_collection.insert_many([
        {"collection_name": "Fantasy", "collection_address": "addr1", "wallet_address": VALID_WALLET, "category": "Art"},
        {"collection_name": "SciFi", "collection_address": "addr2", "wallet_address": VALID_WALLET_2, "category": "Digital"},
        {"collection_name": "Nature", "collection_address": "addr3", "wallet_address": VALID_WALLET, "category": "Photography"},
    ])

    # Test 7a: Search by query
    resp = client.get("/search?q=dragon")
    record("search by query", resp.status_code == 200, f"status={resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        record("search finds dragon", data.get("total") == 1, f"total={data.get('total')}")

    # Test 7b: Search by creator
    resp = client.get(f"/search?creator={VALID_WALLET}")
    record("search by creator", resp.status_code == 200, f"status={resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        record("search finds creator nfts", data.get("total") == 3, f"total={data.get('total')}")

    # Test 7c: Search by collection
    resp = client.get("/search?collection=Fantasy")
    record("search by collection", resp.status_code == 200, f"status={resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        record("search finds collection nfts", data.get("total") == 2, f"total={data.get('total')}")

    # Test 7d: Search by style
    resp = client.get("/search?style=anime")
    record("search by style", resp.status_code == 200, f"status={resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        record("search finds style nfts", data.get("total") == 1, f"total={data.get('total')}")

    # Test 7e: Search by category
    resp = client.get("/search?category=Art")
    record("search by category", resp.status_code == 200, f"status={resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        record("search finds category nfts", data.get("total") == 2, f"total={data.get('total')}")

    # Test 7f: Search with no results
    resp = client.get("/search?q=nonexistent_xyz")
    record("search no results", resp.status_code == 200, f"status={resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        record("search empty result", data.get("total") == 0, f"total={data.get('total')}")

    # Test 7g: Search with pagination
    resp = client.get("/search?page=1&limit=2")
    record("search with pagination", resp.status_code == 200, f"status={resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        record("pagination limit respected", len(data.get("results", [])) <= 2, f"count={len(data.get('results', []))}")
        record("pagination pages correct", data.get("pages") == 2, f"pages={data.get('pages')}")

# ============================================================
# Test 8: Transaction History & NFT Details
# ============================================================
def test_transaction_history_and_nft_detail():
    print("\n=== TEST: Transaction History & NFT Details ===")
    cleanup()

    headers = auth_header()
    headers2 = auth_header(VALID_WALLET_2)

    # Create, list, and sell an NFT to generate a transaction
    nft_result = image_collection.insert_one({
        "wallet_address": VALID_WALLET,
        "name": "TX Test NFT",
        "image": "https://test.com/tx.png",
        "description": "Transaction test",
    })
    nft_id = str(nft_result.inserted_id)

    resp = client.post("/list-nft", json={
        "nft_id": nft_id, "listing_type": "fixed", "price": 75,
    }, headers=headers)
    listing_id = resp.json().get("listing_id")

    client.post("/buy-nft", json={"listing_id": listing_id}, headers=headers2)

    # Test 8a: Get transaction history for seller
    resp = client.get(f"/transaction-history/{VALID_WALLET}")
    record("get seller tx history", resp.status_code == 200, f"status={resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        record("seller has transactions", data.get("total", 0) > 0, f"total={data.get('total')}")

    # Test 8b: Get transaction history for buyer
    resp = client.get(f"/transaction-history/{VALID_WALLET_2}")
    record("get buyer tx history", resp.status_code == 200, f"status={resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        record("buyer has transactions", data.get("total", 0) > 0, f"total={data.get('total')}")

    # Test 8c: Get NFT detail
    resp = client.get(f"/nft/{nft_id}")
    record("get nft detail", resp.status_code == 200, f"status={resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        record("nft detail has transactions", "transactions" in data, f"keys={data.keys()}")
        record("nft detail has name", data.get("name") == "TX Test NFT", f"name={data.get('name')}")

    # Test 8d: Get non-existent NFT
    resp = client.get("/nft/000000000000000000000000")
    record("get non-existent nft returns 404", resp.status_code == 404, f"status={resp.status_code}")

    # Test 8e: Get NFTs by wallet
    resp = client.get(f"/nfts/{VALID_WALLET_2}")
    record("get nfts by wallet", resp.status_code == 200, f"status={resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        record("wallet has nfts", data.get("total", 0) > 0, f"total={data.get('total')}")

# ============================================================
# Test 9: Utility Endpoints
# ============================================================
def test_utility():
    print("\n=== TEST: Utility Endpoints ===")
    cleanup()

    # Test 9a: Store email
    resp = client.post("/store-email", data={"email": "test@example.com"})
    record("store email", resp.status_code == 201, f"status={resp.status_code}, body={resp.text}")

    # Test 9b: Store duplicate email
    resp = client.post("/store-email", data={"email": "test@example.com"})
    record("duplicate email returns 409", resp.status_code == 409, f"status={resp.status_code}")

    # Test 9c: Store email with wallet
    resp = client.post("/store-email", data={"email": "test2@example.com", "wallet_address": VALID_WALLET})
    record("store email with wallet", resp.status_code == 201, f"status={resp.status_code}")

    # Test 9d: Get fees
    resp = client.get("/fees")
    record("get fees", resp.status_code == 200, f"status={resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        record("fees has currency", data.get("currency") == "FRY", f"currency={data.get('currency')}")
        record("fees has transaction_fee_percent", "transaction_fee_percent" in data, f"keys={data.keys()}")

    # Test 9e: Get index page
    resp = client.get("/")
    record("get index page", resp.status_code == 200, f"status={resp.status_code}")

    # Test 9f: Upload NFT image (with auth)
    headers = auth_header()
    fake_image = io.BytesIO(b"fake image data")
    resp = client.post("/upload-nft-image", files={"image": ("test.png", fake_image, "image/png")}, headers=headers)
    record("upload nft image", resp.status_code == 200, f"status={resp.status_code}, body={resp.text}")

    # Test 9g: Upload metadata (with auth)
    resp = client.post("/upload-metadata", json={
        "image": "https://test.com/test.png",
        "name": "Test",
    }, headers=headers)
    record("upload metadata", resp.status_code == 200, f"status={resp.status_code}, body={resp.text}")

    # Test 9h: Upload multiple images (with auth)
    fake1 = io.BytesIO(b"img1")
    fake2 = io.BytesIO(b"img2")
    resp = client.post("/upload-images", files=[
        ("images", ("img1.png", fake1, "image/png")),
        ("images", ("img2.png", fake2, "image/png")),
    ], headers=headers)
    record("upload multiple images", resp.status_code == 200, f"status={resp.status_code}, body={resp.text}")

# ============================================================
# Test 10: Cancel listing
# ============================================================
def test_cancel_listing():
    print("\n=== TEST: Cancel Listing ===")
    cleanup()

    headers = auth_header()

    # Create an NFT and list it
    nft_result = image_collection.insert_one({
        "wallet_address": VALID_WALLET,
        "name": "Cancel Test NFT",
        "image": "https://test.com/cancel.png",
    })
    nft_id = str(nft_result.inserted_id)

    resp = client.post("/list-nft", json={
        "nft_id": nft_id, "listing_type": "fixed", "price": 100,
    }, headers=headers)
    listing_id = resp.json().get("listing_id")

    # Cancel it
    resp = client.put(f"/cancel-listing/{listing_id}", headers=headers)
    record("cancel listing", resp.status_code == 200, f"status={resp.status_code}, body={resp.text}")

    # Try to cancel again
    resp = client.put(f"/cancel-listing/{listing_id}", headers=headers)
    record("cancel already cancelled returns 400", resp.status_code == 400, f"status={resp.status_code}")

    # Try to buy cancelled listing
    headers2 = auth_header(VALID_WALLET_2)
    resp = client.post("/buy-nft", json={"listing_id": listing_id}, headers=headers2)
    record("buy cancelled listing returns 400", resp.status_code == 400, f"status={resp.status_code}")

# ============================================================
# Test 11: Edge Cases
# ============================================================
def test_edge_cases():
    print("\n=== TEST: Edge Cases ===")
    cleanup()

    headers = auth_header()

    # Test invalid ObjectId formats
    resp = client.get("/nft/not-an-objectid")
    record("invalid nft id format returns 400", resp.status_code == 400, f"status={resp.status_code}")

    resp = client.get("/get-listing/not-an-id")
    record("invalid listing id format returns 400", resp.status_code == 400, f"status={resp.status_code}")

    resp = client.get("/collection-nfts/not-an-id")
    record("invalid collection id format returns 400", resp.status_code == 400, f"status={resp.status_code}")

    # Test list NFT with invalid listing_type
    nft_result = image_collection.insert_one({
        "wallet_address": VALID_WALLET,
        "name": "Edge Case NFT",
        "image": "https://test.com/edge.png",
    })
    resp = client.post("/list-nft", json={
        "nft_id": str(nft_result.inserted_id),
        "listing_type": "invalid_type",
        "price": 100,
    }, headers=headers)
    record("invalid listing_type returns 400", resp.status_code == 400, f"status={resp.status_code}")

    # Test place bid with missing fields
    resp = client.post("/place-bid", json={}, headers=headers)
    record("bid missing fields returns 400", resp.status_code == 400, f"status={resp.status_code}")

    # Test end auction on non-auction listing
    nft_result2 = image_collection.insert_one({
        "wallet_address": VALID_WALLET,
        "name": "Fixed Price NFT",
        "image": "https://test.com/fixed.png",
    })
    resp = client.post("/list-nft", json={
        "nft_id": str(nft_result2.inserted_id),
        "listing_type": "fixed",
        "price": 50,
    }, headers=headers)
    if resp.status_code == 200:
        fixed_listing_id = resp.json()["listing_id"]
        resp = client.post(f"/end-auction/{fixed_listing_id}", headers=headers)
        record("end auction on fixed listing returns 400", resp.status_code == 400, f"status={resp.status_code}")

    # Test pagination edge cases
    resp = client.get("/search?page=999&limit=10")
    record("search empty page", resp.status_code == 200, f"status={resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        record("empty page returns 0 results", len(data.get("results", [])) == 0, f"count={len(data.get('results', []))}")

# ============================================================
# Run All Tests
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("FRY MARKET BACKEND - QA TEST SUITE")
    print("=" * 60)

    test_authentication()
    test_profiles()
    test_collections()
    test_nft_listing()
    test_buy_nft()
    test_auctions()
    test_search()
    test_transaction_history_and_nft_detail()
    test_utility()
    test_cancel_listing()
    test_edge_cases()

    print("\n" + "=" * 60)
    print(f"RESULTS: {len(PASSED)} PASSED, {len(ISSUES)} FAILED")
    print("=" * 60)

    if ISSUES:
        print("\nFAILED TESTS:")
        for issue in ISSUES:
            print(f"  - {issue['test']}: {issue['detail']}")

    print(f"\nTotal: {len(PASSED) + len(ISSUES)} tests")
    sys.exit(1 if ISSUES else 0)
