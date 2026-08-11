import requests

def test_http_connection() -> None:
    """
    Test basic HTTP connectivity to ensure the environment is working.
    """
    print("Testing HTTP connection...")
    try:
        response = requests.get("https://httpbin.org/get", timeout=5)
        if response.status_code == 200:
            print("[OK] HTTP connection successful!")
        else:
            print(f"[ERROR] HTTP request failed with status code: {response.status_code}")
    except requests.RequestException as e:
        print(f"[ERROR] Network error: {e}")

def main() -> None:
    """
    Main entry point for the networking lab.
    """
    # 1. Test HTTP
    test_http_connection()

    # 2. MQTT

    # 3. Modbus

if __name__ == "__main__":
    main()
