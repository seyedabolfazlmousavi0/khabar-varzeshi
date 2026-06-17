from google.genai import types, Client

# تنظیم کلاینت برای استفاده از API گپ جی‌پی‌تی
client = Client(
    api_key='sk-eJGoGzKthRUB6KXLLxpoFpEf00ju8NKzJksaRK1hiAgk2lra',
    http_options=types.HttpOptions(base_url='https://api.gapgpt.app/')
)

# استفاده از مدل
response = client.models.generate_content(
    model='gemini-2.5-pro',
    contents='سلام!'
)

print(response.text)