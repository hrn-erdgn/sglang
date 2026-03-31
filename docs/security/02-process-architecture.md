# FAZE 2: Süreç Mimarisi ve Veri Akışı

## 2.1 Süreç Haritası (tp=1, dp=4)

### Başlatma Zinciri

```
python -m sglang.launch_server
  → srt/entrypoints/http_server.py: launch_server()
    → srt/entrypoints/engine.py: Engine.__init__()
      → PortArgs.init_new()  →  6 adet IPC soketi oluşturulur
      → TokenizerManager başlatılır (PULL + PUSH soketleri)
      → DetokenizerManager başlatılır (PULL + PUSH soketleri)
      → DataParallelController başlatılır
        → 4 adet Scheduler süreci başlatılır (DP0-DP3)
          → Her Scheduler: PULL + PUSH + PUSH + DEALER soketleri
          → Her Scheduler: 1 ModelRunner (1 GPU)
      → Engine: DEALER soketi (RPC)
    → FastAPI/uvicorn: HTTP server port 30000
```

### Süreç Listesi

| # | Süreç | PID Tipi | GPU | ZMQ Soketleri | Dosya |
|---|-------|----------|-----|---------------|-------|
| 1 | HTTP Server (main) | Ana süreç | Yok | — | `http_server.py` |
| 2 | Engine | Ana süreç içinde | Yok | 1 DEALER (rpc_ipc) | `engine.py:214` |
| 3 | TokenizerManager | Thread/Coroutine | Yok | 1 PULL + 1 PUSH | `tokenizer_manager.py:314-319` |
| 4 | DetokenizerManager | Thread | Yok | 1 PULL + 1 PUSH | `detokenizer_manager.py:96-99` |
| 5 | DataParallelController | Thread | Yok | 1 PULL + 4 PUSH | `data_parallel_controller.py:131-256` |
| 6 | Scheduler DP0 | Ayrı süreç | GPU 0 | 1 PULL + 2 PUSH + 1 DEALER | `scheduler.py:455-499` |
| 7 | Scheduler DP1 | Ayrı süreç | GPU 1 | 1 PULL + 2 PUSH + 1 DEALER | `scheduler.py:455-499` |
| 8 | Scheduler DP2 | Ayrı süreç | GPU 2 | 1 PULL + 2 PUSH + 1 DEALER | `scheduler.py:455-499` |
| 9 | Scheduler DP3 | Ayrı süreç | GPU 3 | 1 PULL + 2 PUSH + 1 DEALER | `scheduler.py:455-499` |

### Veri Akışı Diyagramı

```
                          ┌─────────────────┐
                          │  HTTP Client     │
                          └────────┬────────┘
                                   │ HTTP (port 30000)
                          ┌────────▼────────┐
                          │  FastAPI Server  │
                          └────────┬────────┘
                                   │
                          ┌────────▼────────┐
                          │     Engine       │
                          │  DEALER (rpc_ipc)│
                          └────────┬────────┘
                                   │ IPC (send_pyobj/recv_pyobj)
                          ┌────────▼────────┐
                          │ TokenizerManager │
                          │ PULL←detok  PUSH→sched│
                          └────────┬────────┘
                                   │ IPC (send_pyobj)
                    ┌──────────────▼──────────────┐
                    │   DataParallelController     │
                    │  PULL←tok    PUSH→workers x4 │
                    └──┬───────┬───────┬───────┬──┘
                       │       │       │       │  IPC (send_pyobj)
                  ┌────▼──┐┌──▼───┐┌──▼───┐┌──▼───┐
                  │Sched 0││Sched1││Sched2││Sched3│
                  │DP0    ││DP1   ││DP2   ││DP3   │
                  │GPU 0  ││GPU 1 ││GPU 2 ││GPU 3 │
                  └───┬───┘└──┬───┘└──┬───┘└──┬───┘
                      │       │       │       │  IPC (send_pyobj)
                    ┌─▼───────▼───────▼───────▼─┐
                    │   DetokenizerManager       │
                    │   PULL←schedulers           │
                    └──────────┬─────────────────┘
                               │ IPC (send_pyobj)
                    ┌──────────▼─────────────────┐
                    │   TokenizerManager          │
                    │   PULL←detokenizer           │
                    └──────────┬─────────────────┘
                               │ HTTP Response
                    ┌──────────▼─────────────────┐
                    │   HTTP Client                │
                    └──────────────────────────────┘
```

**Tüm oklar IPC** (UNIX domain socket). Mesaj formatı: ZMQ `send_pyobj()`/`recv_pyobj()` = **pickle**.

---

## 2.2 Mesaj Serialization Formatları

### ZMQ send_pyobj/recv_pyobj

ZMQ'nun `send_pyobj()` ve `recv_pyobj()` metotları dahili olarak Python'un `pickle` modülünü kullanır:

```python
# pyzmq kaynak kodu (basitleştirilmiş):
def send_pyobj(self, obj, flags=0, protocol=DEFAULT_PROTOCOL):
    msg = pickle.dumps(obj, protocol)
    return self.send(msg, flags)

def recv_pyobj(self, flags=0):
    msg = self.recv(flags)
    return pickle.loads(msg)
```

Bu demektir ki her `recv_pyobj()` çağrısı bir `pickle.loads()` çağrısıdır ve aynı RCE riskini taşır.

### Özel Serialization (disaggregation)

| Bileşen | Format | Güvenli mi? |
|---------|--------|-------------|
| KV transfer (mooncake) | Struct unpacking (`from_zmq()`) | Evet |
| KV transfer (nixl) | Binary struct | Evet |
| Embedding transfer (zmq) | `pickle.dumps()` + raw tensor bytes (multipart) | **HAYIR** |
| Bootstrap HTTP | JSON | Evet |
| gRPC | Protocol Buffers | Evet |

---

## 2.3 Netstat'taki 16 Port Analizi

Kullanıcının gördüğü 16 port (12 TCP on 10.20.20.28, 4 TCP6 on :::):

### TCP Portların Kaynakları

| Port | Kaynak | Bind Adresi | Amaç | Pickle? |
|------|--------|-------------|-------|---------|
| 30000 | FastAPI/uvicorn | `0.0.0.0:30000` (`--host 0.0.0.0`) | HTTP API | Hayır (JSON) |
| Rastgele #1 | NCCL (DP0) | `127.0.0.1:{port}` | torch.distributed init | Hayır |
| Rastgele #2 | NCCL (DP1) | `127.0.0.1:{port}` | torch.distributed init | Hayır |
| Rastgele #3 | NCCL (DP2) | `127.0.0.1:{port}` | torch.distributed init | Hayır |
| Rastgele #4 | NCCL (DP3) | `127.0.0.1:{port}` | torch.distributed init | Hayır |
| Rastgele #5-8 | MessageQueue local XPUB (x4) | `127.0.0.1:{port}` | SHM overflow channel | Evet (pickle) |
| Rastgele #9-12 | torch.distributed/NCCL internal | `10.20.20.28` veya `127.0.0.1` | Kolektif iletişim | Hayır |
| ::: #1-4 | torch.distributed TCP6 | `:::` (tüm IPv6) | NCCL/Gloo backend | Hayır |

### Önemli Notlar

1. **ZMQ IPC soketleri netstat'ta GÖRÜNMEZ** — UNIX domain socket kullanır, `/tmp/` altında dosya olarak bulunur
2. **Port 30000 tek gerçek dış erişimli port** — `--host 0.0.0.0` nedeniyle internet'ten erişilebilir
3. **NCCL portları** genelde `127.0.0.1`'e bind eder (`model_runner.py:920-922`):
   ```python
   dist_init_method = NetworkAddress(
       self.server_args.host or "127.0.0.1", self.dist_port
   ).to_tcp()
   ```
   AMA: `--host 0.0.0.0` kullanıldığında bu `tcp://0.0.0.0:{port}` olabilir — **tehlikeli!**

4. **10.20.20.28'de dinleyen portlar** muhtemelen NCCL'nin `get_local_ip_auto()` ile çözdüğü IP'ye bind etmesinden kaynaklanıyor

### Doğrulama Komutu

```bash
# Hangi port hangi sürece ait:
ss -tlnp | grep python

# ZMQ IPC soketlerini görmek için:
ls -la /tmp/tmp* | head -20
lsof -U | grep python | grep tmp
```

---

## 2.4 TokenizerManager ↔ Scheduler İletişimi

### Kanal Detayları

| Yön | Soket | Transport | Mesaj Tipi |
|-----|-------|-----------|------------|
| Tokenizer → Scheduler | PUSH → PULL | IPC | Tokenize edilmiş request objesi |
| Scheduler → Tokenizer | PUSH → PULL (detok üzerinden) | IPC | Batch output objesi |

### Mesaj Validation

**Gelen mesajlara validation UYGULANMIYOR.** `recv_pyobj()` sonrası obje doğrudan işlenir:

```python
# scheduler.py:1413
recv_req = self.recv_from_tokenizer.recv_pyobj(zmq.NOBLOCK)
# Doğrudan isinstance() kontrolü yapılır ama bu pickle RCE'yi önlemez
```

`recv_pyobj()` zaten pickle.loads() çağrısı yapar — kötücül pickle verisi `recv_pyobj()` dönmeden ÖNCE çalışır.

---

## 2.5 DetokenizerManager ↔ Scheduler İletişimi

Aynı pattern:

```python
# detokenizer_manager.py:141
recv_obj = self.recv_from_scheduler.recv_pyobj()
```

Validation yok. IPC kanalında olduğu sürece risk düşük, ama IPC→TCP geçişinde kritik hale gelir.

---

## 2.6 DataParallelController İç İletişimi

```python
# data_parallel_controller.py:177-180
def send_to_all_workers(self, obj):
    for i, worker in enumerate(self.workers):
        if self.status[i]:
            worker.send_pyobj(obj)  # PUSH soketi ile her worker'a pickle gönderir
```

DPController, TokenizerManager'dan `recv_pyobj()` ile istek alır (satır 568), round-robin veya load-balance ile worker'lara dağıtır.

Multi-node DP attention modunda port broadcast:
- Node 0: `rep_socket.send_pyobj(worker_ports)` — satır 337
- Diğer node'lar: `req_socket.recv_pyobj()` — satır 361
- Bu kanal TCP üzerinden çalışır ve **pickle ile port listesi gönderir** — RCE vektörü!
