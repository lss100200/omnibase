"""End-to-end RAG test: register → upload → search.

Creates a test user, uploads a text file (as PDF substitute),
waits for ingest, then searches.
"""
import json
import urllib.request

BASE = "http://localhost:8000/api"

# 1. Register
print("=== 1. Register ===")
body = json.dumps({
    "email": "ragtest@test.com",
    "password": "RagTest2024",
}).encode()
req = urllib.request.Request(
    f"{BASE}/auth/register",
    data=body,
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    r = urllib.request.urlopen(req, timeout=30)
    data = json.loads(r.read().decode())
    token = data["access_token"]
    print(f"  ✅ Registered: {data['user']['email']}")
    print(f"  Tenant: {data['tenant']['slug']}")
except urllib.error.HTTPError as e:
    err = e.read().decode()
    if "already" in err.lower() or "conflict" in err.lower():
        print("  User exists, trying login...")
        body = json.dumps({
            "email": "ragtest@test.com",
            "password": "RagTest2024",
        }).encode()
        req = urllib.request.Request(
            f"{BASE}/auth/login",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        r = urllib.request.urlopen(req, timeout=30)
        data = json.loads(r.read().decode())
        token = data["access_token"]
        print(f"  ✅ Logged in: {data['user']['email']}")
    else:
        print(f"  ❌ Register failed: {e.code} {err[:200]}")
        exit(1)

# 2. Upload a text file
print("\n=== 2. Upload text file ===")
test_content = """
OmniBase AI RAG Test Document

OmniBase is a self-hosted, AI-native personal knowledge workbench.
It uses PostgreSQL with pgvector for vector storage and retrieval.

The core architecture includes:
1. Multi-tenant schema-per-tenant isolation
2. JWT authentication with bcrypt password hashing
3. MinIO for object storage of uploaded files
4. Celery workers for async document processing
5. BGE-small-zh-v1.5 for embedding generation (512 dimensions)
6. BGE-reranker-v2-m3 for precision reranking
7. DeepSeek or Zhipu GLM for LLM answer generation

The retrieval pipeline uses a cascade architecture:
- Level 0: Query routing via small embedding model
- Level 1: Coarse recall via HNSW vector search (top 100)
- Level 2: Precision reranking via cross-encoder (top 5)
- Level 3: LLM answer generation with citation support

This document tests the full ingest pipeline: parse, chunk, embed, store.
"""

# Create a multipart form upload
boundary = "----TestBoundary1234567890"
body_parts = []
body_parts.append(f"--{boundary}\r\n")
body_parts.append(
    'Content-Disposition: form-data; name="file"; filename="rag_test.txt"\r\n'
)
body_parts.append("Content-Type: text/plain\r\n\r\n")
body_parts.append(test_content)
body_parts.append(f"\r\n--{boundary}--\r\n")

upload_body = "".join(body_parts).encode("utf-8")

req = urllib.request.Request(
    f"{BASE}/documents",
    data=upload_body,
    headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Authorization": f"Bearer {token}",
    },
    method="POST",
)
try:
    r = urllib.request.urlopen(req, timeout=120)
    data = json.loads(r.read().decode())
    doc = data["document"]
    print(f"  ✅ Uploaded: {doc['filename']}")
    print(f"  Status: {doc['status']}")
    print(f"  Chunks: {doc.get('metadata', {}).get('rag_chunks', 'N/A')}")
    doc_id = doc["id"]
except urllib.error.HTTPError as e:
    print(f"  ❌ Upload failed: {e.code}")
    print(f"  {e.read().decode()[:500]}")
    exit(1)

# 3. Search
print("\n=== 3. RAG Search ===")
body = json.dumps({"query": "What embedding model does OmniBase use?", "top_k": 3}).encode()
req = urllib.request.Request(
    f"{BASE}/rag/search",
    data=body,
    headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    },
    method="POST",
)
try:
    r = urllib.request.urlopen(req, timeout=60)
    data = json.loads(r.read().decode())
    print(f"  ✅ Search returned {data['total_found']} results in {data['latency_ms']}ms")
    for i, result in enumerate(data["results"]):
        preview = result["content"][:100].replace("\n", " ")
        print(f"  [{i+1}] score={result['score']:.4f} | {preview}...")
except urllib.error.HTTPError as e:
    print(f"  ❌ Search failed: {e.code}")
    print(f"  {e.read().decode()[:500]}")

print("\n=== Done ===")
