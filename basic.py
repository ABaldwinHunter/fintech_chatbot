from anthropic import Anthropic

client = Anthropic()

response = client.messages.create(
    model="claude-haiku-4-5",
    max_tokens=500,
    system="You are Finn, a helpful banking support assistant.",
    messages=[
        {
            "role": "user",
            "content": "Why is my ACH transfer pending?"
        }
    ],
)

print(response.content[0].text)
