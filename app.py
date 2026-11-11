from flask import Flask, render_template, request, jsonify
from datetime import datetime, timezone
from pathlib import Path
import ipaddress, sqlite3
from monitor import ping_host

app = Flask(__name__)
DB = Path(__file__).with_name("network_monitor.db")
def now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")
def db():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; c.execute("PRAGMA foreign_keys=ON"); return c

def init_db():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS devices(id INTEGER PRIMARY KEY, name TEXT NOT NULL, ip_address TEXT UNIQUE NOT NULL, criticality TEXT NOT NULL DEFAULT 'normal', status TEXT NOT NULL DEFAULT 'unknown', last_latency_ms REAL, last_checked TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS checks(id INTEGER PRIMARY KEY, device_id INTEGER NOT NULL, status TEXT NOT NULL, latency_ms REAL, checked_at TEXT NOT NULL, FOREIGN KEY(device_id) REFERENCES devices(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS incidents(id INTEGER PRIMARY KEY, device_id INTEGER NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL, severity TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL, acknowledged_at TEXT, resolved_at TEXT, FOREIGN KEY(device_id) REFERENCES devices(id) ON DELETE CASCADE);
        ''')
        if not c.execute("SELECT id FROM devices WHERE ip_address='127.0.0.1'").fetchone():
            c.execute("INSERT INTO devices(name,ip_address,criticality,status,created_at) VALUES(?,?,?,?,?)",("Local Machine","127.0.0.1","critical","unknown",now()))

@app.get('/')
def index(): return render_template('index.html')

@app.get('/api/dashboard')
def dashboard():
    with db() as c:
        devices=c.execute("SELECT * FROM devices ORDER BY id").fetchall()
        incidents=c.execute("SELECT i.*,d.name device_name,d.ip_address FROM incidents i JOIN devices d ON d.id=i.device_id ORDER BY CASE i.status WHEN 'open' THEN 0 WHEN 'acknowledged' THEN 1 ELSE 2 END,i.created_at DESC LIMIT 100").fetchall()
        stats={"total_devices":c.execute("SELECT COUNT(*) FROM devices").fetchone()[0],"online_devices":c.execute("SELECT COUNT(*) FROM devices WHERE status='online'").fetchone()[0],"offline_devices":c.execute("SELECT COUNT(*) FROM devices WHERE status='offline'").fetchone()[0],"open_incidents":c.execute("SELECT COUNT(*) FROM incidents WHERE status IN ('open','acknowledged')").fetchone()[0]}
        history=c.execute("SELECT c.checked_at,c.latency_ms,c.status,d.name FROM checks c JOIN devices d ON d.id=c.device_id ORDER BY c.id DESC LIMIT 30").fetchall()
    return jsonify(devices=[dict(x) for x in devices],incidents=[dict(x) for x in incidents],stats=stats,history=[dict(x) for x in reversed(history)])

@app.post('/api/devices')
def add_device():
    data=request.get_json(silent=True) or {}; name=str(data.get('name','')).strip(); raw=str(data.get('ip_address','')).strip(); criticality=data.get('criticality','normal')
    if not name or len(name)>60: return jsonify(error='Enter a device name (1–60 characters).'),400
    try: ip=str(ipaddress.ip_address(raw))
    except ValueError: return jsonify(error='Enter a valid IPv4 or IPv6 address.'),400
    if criticality not in ('normal','critical'): return jsonify(error='Invalid criticality.'),400
    try:
        with db() as c: cur=c.execute("INSERT INTO devices(name,ip_address,criticality,status,created_at) VALUES(?,?,?,'unknown',?)",(name,ip,criticality,now())); did=cur.lastrowid
    except sqlite3.IntegrityError: return jsonify(error='That IP address is already registered.'),409
    return jsonify(message='Device added.',device_id=did),201

@app.post('/api/monitor')
def run_monitoring():
    results=[]
    with db() as c:
        devices=c.execute("SELECT * FROM devices ORDER BY id").fetchall()
        for d in devices:
            r=ping_host(d['ip_address']); status='online' if r['reachable'] else 'offline'; stamp=now()
            c.execute("INSERT INTO checks(device_id,status,latency_ms,checked_at) VALUES(?,?,?,?)",(d['id'],status,r['latency_ms'],stamp))
            c.execute("UPDATE devices SET status=?,last_latency_ms=?,last_checked=? WHERE id=?",(status,r['latency_ms'],stamp,d['id']))
            if status=='offline':
                active=c.execute("SELECT id FROM incidents WHERE device_id=? AND status IN ('open','acknowledged')",(d['id'],)).fetchone()
                if not active:
                    severity='P1' if d['criticality']=='critical' else 'P2'
                    c.execute("INSERT INTO incidents(device_id,title,description,severity,status,created_at) VALUES(?,?,?,?, 'open',?)",(d['id'],f"Device unreachable: {d['name']}",f"No ICMP response from {d['ip_address']}. Check device availability, connectivity, routing and firewall rules.",severity,stamp))
            elif d['status']=='offline':
                c.execute("UPDATE incidents SET status='resolved',resolved_at=?,description=description || ' Automatically resolved after connectivity recovered.' WHERE device_id=? AND status IN ('open','acknowledged')",(stamp,d['id']))
            results.append({"device_id":d['id'],"name":d['name'],"ip_address":d['ip_address'],"status":status,"latency_ms":r['latency_ms'],"detail":r['detail']})
    return jsonify(message='Monitoring cycle completed.',results=results)

@app.patch('/api/incidents/<int:iid>')
def update_incident(iid):
    action=(request.get_json(silent=True) or {}).get('action'); stamp=now()
    with db() as c:
        row=c.execute("SELECT * FROM incidents WHERE id=?",(iid,)).fetchone()
        if not row: return jsonify(error='Incident not found.'),404
        if action=='acknowledge':
            if row['status']=='resolved': return jsonify(error='Resolved incidents cannot be acknowledged.'),400
            c.execute("UPDATE incidents SET status='acknowledged',acknowledged_at=? WHERE id=?",(stamp,iid))
        elif action=='resolve': c.execute("UPDATE incidents SET status='resolved',resolved_at=? WHERE id=?",(stamp,iid))
        else: return jsonify(error='Action must be acknowledge or resolve.'),400
    return jsonify(message=f'Incident {action}d successfully.')

@app.delete('/api/devices/<int:did>')
def delete_device(did):
    with db() as c:
        cur=c.execute("DELETE FROM devices WHERE id=?",(did,))
        if not cur.rowcount: return jsonify(error='Device not found.'),404
    return jsonify(message='Device and history deleted.')

if __name__=='__main__':
    init_db(); print('Open http://127.0.0.1:5000'); app.run(debug=True,use_reloader=False)
