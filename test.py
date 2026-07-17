import os
import httpx
from dotenv import load_dotenv

load_dotenv()

# Your verified endpoint and key config
PROJECT_ENDPOINT = "https://nirnu-itopssmartmonitor.services.ai.azure.com/api/projects/proj-default"
API_KEY = os.environ.get("OPENAI_API_KEY")

# Clean the endpoint and point directly to the deployments REST API
base_url = PROJECT_ENDPOINT.rstrip("/")
deployments_url = f"{base_url}/deployments?api-version=2024-10-21-preview"

headers = {
    "api-key": API_KEY,
    "Content-Type": "application/json"
}

print("Fetching model deployments via direct HTTP REST call...")
try:
    with httpx.Client() as client:
        response = client.get(deployments_url, headers=headers)
        
    if response.status_code == 200:
        data = response.json()
        deployments = data.get("value", [])
        
        print("\n--- Active Deployments Found ---")
        if not deployments:
            print("No models deployed in this project yet.")
        for d in deployments:
            # Prints out the name you need for your .env file
            print(f"Deployment Name: {d.get('name')} | Model: {d.get('modelName')}")
    else:
        print(f"\nFailed to get deployments. HTTP {response.status_code}: {response.text}")
        
except Exception as e:
    print(f"\nAn error occurred: {e}")