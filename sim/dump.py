"""Print full transcripts: python dump.py results/run3.json [id-substring ...]"""
import json, sys
r = json.load(open(sys.argv[1]))
for sid, res in r.items():
    if len(sys.argv) > 2 and not any(k in sid for k in sys.argv[2:]):
        continue
    print(("PASS" if res["passed"] else "FAIL"), sid, "-", res["title"])
    for st in res["steps"]:
        s = st["step"]
        if "say" in s:
            print(f"   [{s.get('user','admin')}] > {s['say']}\n        < [{st.get('response_type')}] {st.get('speech')}  ({st.get('latency_ms')} ms)")
        elif "check" not in s:
            print("   ·", {k: v for k, v in s.items() if k not in ("settle",)})
        for c in st.get("calls", []) or []:
            print("          call", c)
        for n in st.get("notifications", []) or []:
            print("          push", n["target"], "|", n["title"], "|", n["message"])
        for n in st.get("spoken", []) or []:
            print("          tts", n["message"])
        for p in st.get("problems") or []:
            print("          ✗", p)
