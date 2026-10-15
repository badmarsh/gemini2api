import json
import os
import sys
import time
from pathlib import Path
import requests

def load_env():
    env_file = Path('.env')
    if not env_file.exists():
        raise FileNotFoundError(".env file not found")
    lines = env_file.read_text(encoding='utf-8-sig').splitlines()
    env = {}
    for line in lines:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env

def test_non_streaming(base_url, api_key, model="gemini-4-argon"):
    url = f"{base_url}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": "Reply with exactly: pong"}
        ],
        "stream": False
    }
    print(f"\n[TEST 1] Non-streaming request to {url} with model '{model}'...")
    start_time = time.time()
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=120)
        elapsed = time.time() - start_time
        print(f"Status Code: {resp.status_code} (took {elapsed:.2f}s)")
        if resp.status_code == 200:
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            print(f"Model returned: {content!r}")
            print(f"Full response object: {json.dumps(data, indent=2, ensure_ascii=False)}")
            return True, data
        else:
            print(f"Error response: {resp.text}")
            return False, resp.text
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"Exception after {elapsed:.2f}s: {type(e).__name__}: {e}")
        return False, str(e)

def test_streaming(base_url, api_key, model="gemini-4-argon"):
    url = f"{base_url}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": "Reply with exactly: pong"}
        ],
        "stream": True
    }
    print(f"\n[TEST 2] Streaming request to {url} with model '{model}'...")
    start_time = time.time()
    chunks = []
    collected_text = ""
    first_token_time = None
    try:
        with requests.post(url, json=payload, headers=headers, stream=True, timeout=120) as resp:
            elapsed_connect = time.time() - start_time
            print(f"Connect status: {resp.status_code} (took {elapsed_connect:.2f}s)")
            if resp.status_code != 200:
                print(f"Error response: {resp.text}")
                return False, resp.text
            
            for line in resp.iter_lines():
                if not line:
                    continue
                line_str = line.decode('utf-8', errors='replace')
                if line_str.startswith("data: "):
                    data_str = line_str[6:].strip()
                    if data_str == "[DONE]":
                        print("Received [DONE]")
                        break
                    try:
                        chunk_json = json.loads(data_str)
                        delta = chunk_json.get("choices", [{}])[0].get("delta", {})
                        if "content" in delta and delta["content"]:
                            if first_token_time is None:
                                first_token_time = time.time() - start_time
                                print(f"First token latency: {first_token_time:.2f}s")
                            collected_text += delta["content"]
                        chunks.append(chunk_json)
                    except json.JSONDecodeError:
                        print(f"Raw line: {line_str}")
        total_time = time.time() - start_time
        print(f"Streaming completed in {total_time:.2f}s, total {len(chunks)} chunks")
        print(f"Collected text: {collected_text!r}")
        return True, collected_text
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"Streaming exception after {elapsed:.2f}s: {type(e).__name__}: {e}")
        return False, str(e)

if __name__ == "__main__":
    env = load_env()
    port = env.get("PORT", "5918")
    api_key = env.get("API_KEY")
    base_url = f"http://localhost:{port}"
    model_to_test = sys.argv[1] if len(sys.argv) > 1 else "gemini-4-argon"
    
    print(f"Targeting: {base_url}")
    print(f"Testing model: {model_to_test}")
    
    ok1, res1 = test_non_streaming(base_url, api_key, model_to_test)
    
    if ok1:
        print("\nWaiting 5 seconds before streaming test...")
        time.sleep(5)
        ok2, res2 = test_streaming(base_url, api_key, model_to_test)
