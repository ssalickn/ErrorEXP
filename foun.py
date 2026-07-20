from openai import OpenAI
 
endpoint = "https://nirnu-itopssmartmonitor.services.ai.azure.com/openai/v1"

deployment_name = "NirnuSmartMonitor_GPT"

api_key = "REPLACE_WITH_YOUR_FOUNDRY_KEY"
 
client = OpenAI(

    base_url=endpoint,

    api_key=api_key

)
 
response = client.responses.create(

    model=deployment_name,

    input="What is the capital of France?",

)
 
print(f"answer: {response.output[0]}")

 