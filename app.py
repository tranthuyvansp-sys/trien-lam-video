import os,io,secrets,hmac
from flask import Flask,request,jsonify,Response,redirect,session,make_response
import psycopg
from psycopg.rows import dict_row
import qrcode
app=Flask(__name__)
app.secret_key=os.getenv("SECRET_KEY","dev-not-secure-change-before-public")
DB=os.getenv("DATABASE_URL","")
ADMIN=os.getenv("ADMIN_PASSWORD","")
def connect():return psycopg.connect(DB,row_factory=dict_row,connect_timeout=10)
def init():
 if not DB:return
 with connect() as c:
  c.execute("CREATE TABLE IF NOT EXISTS videos(id integer PRIMARY KEY,title text NOT NULL,media bytea,active boolean NOT NULL DEFAULT false)")
  c.execute("CREATE TABLE IF NOT EXISTS votes(id bigserial PRIMARY KEY,video_id integer NOT NULL,voter text NOT NULL,score integer CHECK(score BETWEEN 1 AND 10),UNIQUE(video_id,voter))")
  for n in range(1,6):c.execute("INSERT INTO videos(id,title) VALUES (%s,%s) ON CONFLICT DO NOTHING",(n,f'Video số {n:02d}'))
init()
def voter():
 v=request.cookies.get("voter","")
 return v if len(v)==48 and all(c in "0123456789abcdef" for c in v) else secrets.token_hex(24)
def respond(data,v):
 r=make_response(jsonify(data))
 if request.cookies.get("voter")!=v:r.set_cookie("voter",v,max_age=2592000,secure=True,httponly=True,samesite="Lax")
 return r
@app.get("/")
def index():return Response(open("index.html",encoding="utf8").read(),mimetype="text/html")
@app.get("/health")
def health():return "OK"
@app.get("/api/videos")
def list_videos():
 v=voter()
 with connect() as c:
  rows=c.execute("SELECT a.id,a.title,a.active,COUNT(b.id)::int AS votes,COALESCE(AVG(b.score),0)::float AS avg FROM videos a LEFT JOIN votes b ON b.video_id=a.id GROUP BY a.id ORDER BY a.id").fetchall()
  mine={x["video_id"]:x["score"] for x in c.execute("SELECT video_id,score FROM votes WHERE voter=%s",(v,))}
 for x in rows:x["mine"]=mine.get(x["id"])
 return respond(rows,v)
@app.get("/media/<int:n>")
def media(n):
 if not 1<=n<=5:return "Không tìm thấy",404
 with connect() as c:r=c.execute("SELECT media FROM videos WHERE id=%s AND active=true",(n,)).fetchone()
 if not r or not r["media"]:return "Video chưa có",404
 data=bytes(r["media"])
 range_h=request.headers.get("Range","")
 headers={"Accept-Ranges":"bytes","Cache-Control":"public, max-age=120"}
 if range_h.startswith("bytes="):
  try:
   begin,end=range_h[6:].split("-",1);start=int(begin) if begin else 0;stop=int(end) if end else len(data)-1
   if start>=len(data) or start<0:return Response(status=416)
   stop=min(stop,len(data)-1)
   headers["Content-Range"]=f"bytes {start}-{stop}/{len(data)}"
   return Response(data[start:stop+1],206,mimetype="video/mp4",headers=headers)
  except Exception:pass
 return Response(data,mimetype="video/mp4",headers=headers)
@app.post("/api/vote")
def vote():
 x=request.get_json(silent=True) or {};n=x.get("id");score=x.get("score")
 if type(n)!=int or not 1<=n<=5 or type(score)!=int or not 1<=score<=10:return jsonify(error="Điểm không hợp lệ"),400
 v=voter()
 with connect() as c:
  active=c.execute("SELECT active FROM videos WHERE id=%s",(n,)).fetchone()
  if not active or not active["active"]:return jsonify(error="Video chưa mở chấm điểm"),400
  try:c.execute("INSERT INTO votes(video_id,voter,score) VALUES(%s,%s,%s)",(n,v,score))
  except psycopg.errors.UniqueViolation:return respond({"error":"Bạn đã chấm video này trên trình duyệt này."},v),409
 return respond({"ok":True},v)
@app.get("/qr.png")
def qr():
 url=os.getenv("PUBLIC_URL") or request.url_root
 out=io.BytesIO();qrcode.make(url).save(out,format="PNG")
 return Response(out.getvalue(),mimetype="image/png",headers={"Content-Disposition":'attachment; filename="ma-qr-trien-lam.png"' if request.args.get("download") else "inline"})
@app.route("/admin",methods=["GET","POST"])
def admin():
 if request.method=="POST":
  if ADMIN and hmac.compare_digest(request.form.get("password",""),ADMIN):session["admin"]=True;return redirect("/admin")
  return "Mật khẩu không đúng",403
 if not session.get("admin") or not ADMIN:
  return '<meta name="viewport" content="width=device-width"><div style="max-width:380px;margin:80px auto;font-family:Arial"><h2>Quản trị video</h2><form method="post"><input type="password" name="password" placeholder="Mật khẩu" required><button>Đăng nhập</button></form></div>'
 with connect() as c:rows=c.execute("SELECT id,title,active FROM videos ORDER BY id").fetchall()
 parts=['<meta name="viewport" content="width=device-width"><div style="max-width:650px;margin:auto;font-family:Arial"><h1>Quản trị video</h1><p><a href="/">Về trang triển lãm</a></p><p>Tải lên video MP4 tối đa 20 MB. Video được mở chấm sau khi tải thành công.</p>']
 for r in rows:
  parts.append(f'<div style="padding:20px;margin:12px 0;border:1px solid #eedddc"><h3>Video {r["id"]:02d} — {"Đã đăng" if r["active"] else "Chờ cập nhật"}</h3><form method="post" action="/admin/upload/{r["id"]}" enctype="multipart/form-data"><input name="title" maxlength="100" value="Video số {r["id"]:02d}" required><p><input type="file" name="video" accept="video/mp4" required></p><button>Tải video lên</button></form></div>')
 parts.append('</div>')
 return ''.join(parts)
@app.post("/admin/upload/<int:n>")
def upload(n):
 if not session.get("admin") or not ADMIN:return "Không có quyền",403
 if not 1<=n<=5:return "Không tìm thấy",404
 f=request.files.get("video")
 if not f or not f.filename.lower().endswith(".mp4"):return "Chỉ nhận MP4",400
 b=f.read(20*1024*1024+1)
 if len(b)>20*1024*1024:return "Video vượt 20MB",413
 if b[4:8]!=b"ftyp":return "Tệp không phải MP4",400
 title=request.form.get("title",f"Video số {n:02d}").strip()[:100]
 with connect() as c:c.execute("UPDATE videos SET title=%s,media=%s,active=true WHERE id=%s",(title,b,n))
 return redirect("/admin")
if __name__=="__main__":app.run(host="0.0.0.0",port=int(os.environ.get("PORT",10000)))
