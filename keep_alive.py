import requests
import time

# URL to your Flask keep-alive endpoint
# For example, if using Render, this might be the URL of your deployed web service
KEEP_ALIVE_URL = "http://your-flask-app-url.onrender.com"

def keep_alive():
    while True:
        try:
            response = requests.get(KEEP_ALIVE_URL)
            if response.status_code == 200:
                print("Keep-alive request successful.")
            else:
                print(f"Received unexpected status code {response.status_code}.")
        except requests.exceptions.RequestException as e:
            print(f"Error during keep-alive request: {e}")
        
        # Wait for 60 seconds before sending the next request
        time.sleep(60)

if __name__ == "__main__":
    keep_alive()
