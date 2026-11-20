from openai import OpenAI
from qdrant_client import QdrantClient
client = OpenAI()
qdrant = QdrantClient("localhost")

def search_docs(question):
    hits = qdrant.search(collection_name="docs", query_vector=embed(question))
    return [h.payload for h in hits]

def embed(text):
    return client.embeddings.create(model="m", input=text).data[0].embedding

def answer(question):
    docs = search_docs(question)
    return client.chat.completions.create(model="m", messages=[{"role": "user", "content": str(docs)}])
