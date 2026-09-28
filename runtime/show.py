import json
d = json.load(open("runtime/c303.json", encoding="utf-8-sig"))
for c in d:
    print("===", c["id"], c["path"], c.get("line"), c.get("start_line"))
    body = c["body"]
    # strip the bulky evidence/script blocks, keep the finding itself
    import re
    body = re.sub(r"<details>.*?</details>", "[evidence omitted]", body, flags=re.S)
    print(body[:2500])
    print()
