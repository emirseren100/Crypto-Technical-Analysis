from pathlib import Path

p = Path("i18n_extended.py")
s = p.read_text(encoding="utf-8")
key = "\n\nEXTENDED_EN: dict[str, str] = {"
i = s.find(key)
if i == -1:
    raise SystemExit("EXTENDED_EN not found")
# keep everything before key
prefix = s[:i].rstrip()
# find end of EXTENDED_EN dict - last line before file end or next section
open_b = s.find("{", i)
depth = 0
end_rm = None
for k in range(open_b, len(s)):
    if s[k] == "{":
        depth += 1
    elif s[k] == "}":
        depth -= 1
        if depth == 0:
            end_rm = k + 1
            break
suffix = s[end_rm:].lstrip("\n")
p.write_text(prefix + "\n", encoding="utf-8")
print("stripped EXTENDED_EN from i18n_extended.py")
