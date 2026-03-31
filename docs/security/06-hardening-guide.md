# FAZE 7: SGLang Hardening Guide

## 6.1 CVSS v3.1 Skorları

### CVE Skorları

| CVE | Vektör Dizgisi | Skor | Seviye |
|-----|---------------|------|--------|
| **CVE-2026-3060** (Encoder Disagg) | AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H | **9.8** | KRİTİK |
| **ShadowMQ Orijinal** (MessageQueue multi-node) | AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H | **9.8** | KRİTİK |
| **DP Attention multi-node** (tüm kanallar TCP) | AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H | **9.8** | KRİTİK |
| **EP/MoE** (Expert Backup) | AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H | **9.8** | KRİTİK |
| **Dumper RPC** (tcp://*:0) | AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H | **9.8** | KRİTİK |
| **CVE-2026-3059** (Multimodal Gen) | AV:L/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H | **8.4** | YÜKSEK |
| **DP Attention tek node** | AV:L/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H | **8.4** | YÜKSEK |
| **CVE-2026-3989** (replay script) | AV:L/AC:L/PR:N/UI:R/S:U/C:H/I:H/A:H | **7.3** | YÜKSEK |
| **IPC kanalları** (normal mod) | AV:L/AC:H/PR:H/UI:N/S:U/C:H/I:H/A:H | **6.3** | ORTA |

### CVSS Açıklamaları

- **AV:N** (Network): TCP üzerinden uzaktan erişilebilir
- **AV:L** (Local): Sadece aynı makineden (localhost veya IPC)
- **AC:L** (Low complexity): ZMQ PUSH bağlantısı ve pickle payload yeterli
- **PR:N** (No privileges): Authentication yok, anonim erişim
- **C:H/I:H/A:H**: Tam RCE — confidentiality, integrity, availability etkilenir

---

## 6.2 Minimum Güvenlik Baseline'ı (Herkes İçin)

### A. Ağ Seviyesi

**1. Firewall kuralları (iptables/nftables):**

```bash
# Sadece HTTP API portunu dışarı aç
iptables -A INPUT -p tcp --dport 30000 -j ACCEPT

# Güvenilir subnet'ten tüm trafiğe izin ver (GPU cluster)
iptables -A INPUT -p tcp -s 10.20.20.0/24 -j ACCEPT

# Localhost trafiğine izin ver
iptables -A INPUT -i lo -j ACCEPT

# Established bağlantılara izin ver
iptables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT

# Geri kalan tüm gelen TCP'yi reddet
iptables -A INPUT -p tcp -j DROP
```

**2. Host binding değişikliği:**

```bash
# ÖNCE (tehlikeli):
python -m sglang.launch_server --host 0.0.0.0 --port 30000 ...

# SONRA (güvenli):
python -m sglang.launch_server --host 127.0.0.1 --port 30000 ...
# + nginx/caddy reverse proxy ile dış erişim
```

**3. Reverse proxy örneği (nginx):**

```nginx
server {
    listen 80;
    server_name api.example.com;

    # Rate limiting
    limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;

    location /v1/ {
        limit_req zone=api burst=20;
        proxy_pass http://127.0.0.1:30000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;

        # SSE için gerekli
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
    }
}
```

### B. İşletim Sistemi Seviyesi

**4. Kullanıcı izolasyonu:**

```bash
# SGLang'ı root olarak çalıştırmayın
useradd -r -s /bin/false sglang
# GPU erişimi için:
usermod -aG video,render sglang
```

**5. IPC soket izinleri:**

```bash
# /tmp altındaki ZMQ IPC dosyalarının izinlerini kontrol edin
chmod 600 /tmp/tmp*  # Sadece owner okuyabilsin
```

### C. Monitoring

**6. Beklenmedik bağlantıları izleyin:**

```bash
# Cron job: Her 5 dakikada bir beklenmedik bağlantıları kontrol et
*/5 * * * * ss -tnp | grep python | grep -v '127.0.0.1' | grep -v '::1' >> /var/log/sglang-connections.log
```

---

## 6.3 Konfigürasyona Özel Ek Önlemler

### Multi-node Deployment (nnodes > 1)

```bash
# 1. VPN/WireGuard tunnel kullanın
wg-quick up wg0
# SGLang'ı WireGuard interface IP'si ile başlatın:
python -m sglang.launch_server \
  --dist-init-addr 10.0.0.1:29500 \  # WireGuard IP
  --host 10.0.0.1 ...

# 2. NCCL interface kısıtlama
export NCCL_SOCKET_IFNAME=wg0        # Sadece WireGuard interface'i kullan
export GLOO_SOCKET_IFNAME=wg0
export NCCL_NET_GDR_LEVEL=0          # GPU Direct RDMA kapatıldığında
```

### PD Disaggregation

```bash
# 1. Bootstrap portunu firewall ile kısıtla
iptables -A INPUT -p tcp --dport 8998 -s 10.20.20.0/24 -j ACCEPT
iptables -A INPUT -p tcp --dport 8998 -j DROP

# 2. Encoder transfer için mooncake yerine nixl tercih edin
# (nixl struct unpacking kullanır, pickle değil)
python -m sglang.launch_server \
  --disaggregation-transfer-backend nixl ...

# 3. Ephemeral port aralığını kısıtlayın ve firewall'dan izin verin
sysctl -w net.ipv4.ip_local_port_range="40000 50000"
iptables -A INPUT -p tcp --dport 40000:50000 -s 10.20.20.0/24 -j ACCEPT
```

### Expert Parallelism (MoE)

```bash
# Expert backup portlarını kısıtla
# PORT_BASE default: 10000
iptables -A INPUT -p tcp --dport 10000:10100 -s 10.20.20.0/24 -j ACCEPT
iptables -A INPUT -p tcp --dport 10000:10100 -j DROP

# Veya PORT_BASE'i değiştirin
export SGLANG_BACKUP_PORT_BASE=55000
```

### DP Attention

```bash
# Port offset'leri bilinen sabit değerler:
# port+233 ... port+238, port+13
# Bunları firewall'dan kısıtlayın:
for p in $(seq 30233 30238) 30013; do
  iptables -A INPUT -p tcp --dport $p -s 10.20.20.0/24 -j ACCEPT
  iptables -A INPUT -p tcp --dport $p -j DROP
done
```

---

## 6.4 İzleme ve Tespit Kuralları

### Log İzleme

```bash
# SGLang loglarında şüpheli pattern'ler:
# 1. Beklenmedik pickle hataları (exploit denemesi göstergesi)
grep -i "unpickle\|pickle\|deserializ\|UnpicklingError" /var/log/sglang/*.log

# 2. Beklenmedik ZMQ bağlantıları
grep -i "zmq.*connect\|zmq.*bind" /var/log/sglang/*.log

# 3. Encoder receiver hataları
grep -i "error signal from encoder\|error_msg\|error_code" /var/log/sglang/*.log
```

### Network Anomali Tespiti

```bash
# Snort/Suricata kuralı: ZMQ magic bytes + pickle header
# ZMQ ZMTP frames start with specific bytes
# Pickle protocol 5 header: \x80\x05

# Örnek Suricata kuralı:
# alert tcp any any -> $HOME_NET any (
#   msg:"Potential pickle exploit via ZMQ";
#   content:"|80 05|";
#   content:"__reduce__";
#   sid:2026060; rev:1;
# )
```

### Prometheus/Grafana Metrikleri

```yaml
# İzlenmesi gereken metrikler:
# 1. Beklenmedik TCP bağlantı sayısı artışı
# 2. ZMQ soket hata oranları
# 3. Scheduler response time anomalileri (exploit sonrası yavaşlama)
```

---

## 6.5 Acil Durum Müdahale Planı

### Exploit Tespit Edildiğinde

1. **Hemen:** Sunucuyu ağdan izole edin (iptables -P INPUT DROP)
2. **5 dakika:** SGLang süreçlerini durdurun
3. **15 dakika:** Process listesini, network bağlantılarını ve dosya değişikliklerini inceleyin
4. **1 saat:** Forensic analiz (memory dump, disk image)
5. **Sonra:** Temiz kurulum, credential rotation, incident report

```bash
# Acil izolasyon:
iptables -P INPUT DROP
iptables -P OUTPUT DROP
iptables -A INPUT -i lo -j ACCEPT
iptables -A OUTPUT -o lo -j ACCEPT

# Şüpheli süreçleri kontrol et:
ps auxf | grep -v grep | grep -E 'python|sglang|curl|wget|nc|bash'
ss -tnp
cat /proc/*/cmdline | tr '\0' ' ' | grep -v sglang
```
