import requests

url = "https://www.tasnimnews.ir/fa/news/1405/04/01/3623426/"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"
}

try:
    print("Sending request to Tasnim...")
    response = requests.get(url, headers=headers, timeout=10)
    print(f"Status Code: {response.status_code}")
    print(f"HTML Length: {len(response.text)}")
    print("First 200 chars of HTML:", response.text[:200].strip())
except Exception as e:
    print(f"Error occurred: {e}")