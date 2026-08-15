import os

from dotenv import load_dotenv
from openai import OpenAI


load_dotenv()

base_url = os.getenv("AZURE_OPENAI_BASE_URL")
deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT")
api_key = os.getenv("AZURE_OPENAI_API_KEY")

print("Base URL:", base_url)
print("Deployment:", deployment)
print("API key loaded:", bool(api_key))

client = OpenAI(
    api_key=api_key,
    base_url=base_url,
)

chunks = [
    "Employees receive 14 annual leave days.",
    "UserService validates JWT authentication tokens.",
    """
    async function createTimesheet(data) {
        return repository.save(data);
    }
    """,
]

response = client.embeddings.create(
    model=deployment,
    input="Hello, world!",
)

embedding = response.data[0].embedding

# print("Embedding dimension:", len(embedding))
# print("First 5:", embedding)

print("data : ", embedding)