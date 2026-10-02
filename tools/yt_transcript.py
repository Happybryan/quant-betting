"""Fetch a YouTube auto-caption transcript as plain text. Usage: python3 tools/yt_transcript.py VIDEO_ID [...]
ponytail: relies on YouTube's undocumented Android innertube client; will break if YouTube changes it."""
import html, json, re, sys, urllib.request

def transcript(v):
    body = json.dumps({"context": {"client": {"clientName": "ANDROID", "clientVersion": "20.10.38", "androidSdkVersion": 30}}, "videoId": v}).encode()
    req = urllib.request.Request("https://www.youtube.com/youtubei/v1/player", data=body, headers={"Content-Type": "application/json", "User-Agent": "com.google.android.youtube/20.10.38 (Linux; U; Android 11)"})
    d = json.load(urllib.request.urlopen(req, timeout=20))
    tracks = d.get("captions", {}).get("playerCaptionsTracklistRenderer", {}).get("captionTracks", [])
    if not tracks:
        raise SystemExit(f"{v}: no captions")
    x = urllib.request.urlopen(tracks[0]["baseUrl"], timeout=20).read().decode()
    words = re.findall(r"<s[^>]*>(.*?)</s>|<p[^>]*>([^<]+)</p>", x)
    return d["videoDetails"]["title"], " ".join(html.unescape((a or b).strip()) for a, b in words)

if __name__ == "__main__":
    for v in sys.argv[1:]:
        title, text = transcript(v)
        open(f"research/videos/{v}.txt", "w").write(title + "\n\n" + text)
        print(v, title, len(text.split()), "words")
