# # 华为云MaaS云Token服务专用

import requests
import json
from cryptography.fernet import Fernet


key = b'KVJXqDbLOz-vbV2m1QqIVV4t3C7Ckjmc9X1mDS9EsPQ='
cipher = Fernet(key)
with open("HWmaas.crypto", "rb") as f:
    data = f.read()
api_key = cipher.decrypt(data).decode("utf-8")
def ai_generator(prompt: str):
    url = "https://api.modelarts-maas.com/v2/chat/completions"  # API地址
    # Send request.
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {api_key}'
    }
    print("\n\n[AI_generater] PROMPT "+prompt+"\n\n")
    data = {
        "model": "deepseek-v4-flash",  # model参数
        "messages": [
            # {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": prompt}
        ]
    }
    response = requests.post(url, headers=headers, data=json.dumps(data), verify=False)

    # Print result.
    print(response.status_code)
    text = (json.loads(response.text))['choices'][0]['message']['content']
    print("\n\n[AI_generater] RESPONSE "+str(text)+"\n\n")
    return text


if __name__ == '__main__':
    print(ai_generator("你好"))