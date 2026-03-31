# FAZE 1: Saldırı Yüzeyi Haritalaması

## 1.1 Pickle Deserializasyon Noktaları

SGLang kod tabanında toplam ~30 adet `pickle.loads()`, `pickle.load()`, `recv_pyobj()` ve `send_pyobj()` çağrısı tespit edildi. Aşağıda hepsi kategorize edilmiştir.

### KRİTİK — Ağdan erişilebilir TCP soketleri üzerinde, authentication yok

| # | Dosya | Satır | Fonksiyon/Sınıf | Çağrı | Veri Kaynağı | Auth |
|---|-------|-------|-----------------|-------|--------------|------|
| 1 | `srt/disaggregation/encode_receiver.py` | 497 | `WaitingImageRequest._try_recv_mm_data()` | `pickle.loads(parts[0])` | TCP ZMQ PULL soketi, multipart mesaj | YOK |
| 2 | `srt/disaggregation/encode_receiver.py` | 735 | `EncodeReceiverInTokenizer._recv_mm_data()` | `pickle.loads(parts[0])` | TCP ZMQ PULL soketi (async), multipart mesaj | YOK |
| 3 | `multimodal_gen/runtime/scheduler_client.py` | 28 | `run_zeromq_broker()` | `pickle.loads(payload)` | TCP ZMQ REP soketi (`tcp://127.0.0.1:{broker_port}`) | YOK |
| 4 | `srt/elastic_ep/expert_backup_manager.py` | 65 | `ExpertBackupManager.__init__()` | `recv_pyobj()` | TCP ZMQ PULL (`tcp://{public_ip}:10000+rank*2`) | YOK |
| 5 | `srt/elastic_ep/expert_backup_client.py` | 76 | `_receive_loop()` | `recv_pyobj()` | TCP ZMQ SUB (`tcp://{remote_ip}:10001+rank*2`) | YOK |
| 6 | `srt/debug_utils/dumper.py` | 1052 | `_ZmqRpcBroadcast` serve_loop | `recv_pyobj()` | TCP ZMQ REP (`tcp://*:0` — tüm interface'ler!) | YOK |
| 7 | `srt/disaggregation/encode_server.py` | 1271 | `run_encoder()` | `recv_pyobj()` | ZMQ PULL (schedule_socket) | YOK |
| 8 | `srt/disaggregation/common/conn.py` | 125-127 | `CommonKVManager.__init__()` | ZMQ PULL bind | TCP `get_zmq_socket_on_host(host=self.local_ip)` | YOK |

### ORTA — IPC kanalları (normal modda ağdan erişilemez, DP attention/multi-node'da TCP'ye geçer)

| # | Dosya | Satır | Fonksiyon/Sınıf | Çağrı | Transport |
|---|-------|-------|-----------------|-------|-----------|
| 9 | `srt/managers/scheduler.py` | 1413 | Scheduler event loop | `recv_from_tokenizer.recv_pyobj()` | IPC (normal) / TCP (dp-attn) |
| 10 | `srt/managers/scheduler.py` | 1422 | Scheduler event loop | `recv_from_rpc.recv_pyobj()` | IPC (normal) / TCP (dp-attn) |
| 11 | `srt/managers/scheduler.py` | 1557 | Scheduler event loop | `recv_from_rpc.send_pyobj(output)` | IPC (normal) / TCP (dp-attn) |
| 12 | `srt/managers/tokenizer_manager.py` | 1098 | `send_to_scheduler` | `send_pyobj(tokenized_obj)` | IPC (normal) / TCP (dp-attn) |
| 13 | `srt/managers/tokenizer_manager.py` | 1524 | recv loop | `recv_from_detokenizer.recv_pyobj()` | IPC (normal) / TCP (dp-attn) |
| 14 | `srt/managers/detokenizer_manager.py` | 141 | recv loop | `recv_from_scheduler.recv_pyobj()` | IPC (normal) / TCP (dp-attn) |
| 15 | `srt/managers/data_parallel_controller.py` | 180 | `send_to_all_workers()` | `worker.send_pyobj(obj)` | IPC (normal) / TCP (dp-attn) |
| 16 | `srt/managers/data_parallel_controller.py` | 337 | `_broadcast_ports_as_server()` | `rep_socket.send_pyobj(worker_ports)` | TCP (multi-node dp-attn only) |
| 17 | `srt/managers/data_parallel_controller.py` | 361 | `_receive_ports_as_client()` | `req_socket.recv_pyobj()` | TCP (multi-node dp-attn only) |
| 18 | `srt/managers/data_parallel_controller.py` | 568 | dispatch loop | `recv_from_tokenizer.recv_pyobj()` | IPC (normal) / TCP (dp-attn) |

### DÜŞÜK — Lokal süreçler arası (shared memory, torch.distributed, subprocess)

| # | Dosya | Satır | Fonksiyon/Sınıf | Çağrı | Transport |
|---|-------|-------|-----------------|-------|-----------|
| 19 | `srt/distributed/device_communicators/shm_broadcast.py` | 453 | `MessageQueue.dequeue()` | `pickle.loads(buf[1:])` | Shared memory ring buffer |
| 20 | `srt/distributed/device_communicators/shm_broadcast.py` | 456 | `MessageQueue.dequeue()` | `pickle.loads(recv)` | ZMQ XPUB/SUB (127.0.0.1 veya public IP) |
| 21 | `srt/distributed/device_communicators/shm_broadcast.py` | 459 | `MessageQueue.dequeue()` | `pickle.loads(recv)` | ZMQ SUB remote reader (public IP!) |
| 22 | `srt/distributed/parallel_state.py` | 1155 | `broadcast_object()` | `pickle.loads(object_tensor.numpy())` | torch.distributed (NCCL/Gloo) |
| 23 | `srt/distributed/utils.py` | 187 | `broadcast_object()` | `pickle.loads(...)` | TCPStore |
| 24 | `srt/distributed/utils.py` | 207 | `point_to_point_object()` | `pickle.loads(self.store.get(key))` | TCPStore |
| 25 | `srt/utils/common.py` | 1228 | `broadcast_pyobj()` | `pickle.loads(serialized_data)` | torch.distributed (NCCL) |
| 26 | `srt/utils/common.py` | 1293 | `point_to_point_pyobj()` | `pickle.loads(serialized_data)` | torch.distributed (NCCL) |
| 27 | `srt/distributed/naive_distributed.py` | 87 | `broadcast()` | `pickle.loads(pybase64.b64decode(...))` | HTTP broadcast |
| 28 | `srt/managers/multi_tokenizer_mixin.py` | 462 | `__init__()` | `pickle.loads(bytes(shm.buf))` | SharedMemory |
| 29 | `srt/checkpoint_engine/update.py` | 186 | `get_metas_from_pickle_file()` | `pickle.load(f)` | Dosyadan okuma |
| 30 | `srt/distributed/device_communicators/custom_all_reduce_utils.py` | 476 | `_subprocess_main()` | `pickle.loads(sys.stdin.buffer.read())` | Subprocess stdin |

### SafeUnpickler Kullanımı

| Dosya | Satır | Fonksiyon | Kullanıyor mu? |
|-------|-------|-----------|----------------|
| `srt/utils/common.py` | 2122-2196 | `SafeUnpickler` sınıfı, `safe_pickle_load()` | TANIMLANIYOR |
| `scripts/playground/replay_request_dump.py` | 58 | `read_records()` | EVET — tek kullanım noktası |
| Diğer tüm ~30 pickle noktası | — | — | **HAYIR — KULLANILMIYOR** |

---

## 1.2 ZMQ Soket Haritası

### Kullanıcının Konfigürasyonu (tp=1, dp=4, normal DP)

Tüm soketler **IPC** (UNIX domain socket) kullanır. TCP üzerinde ZMQ soketi **yoktur**.

| Süreç | Soket Tipi | Endpoint | Bind/Connect | Pickle? | Dosya:Satır |
|-------|-----------|----------|--------------|---------|-------------|
| TokenizerManager | PULL | `ipc:///tmp/xxx` (tokenizer_ipc) | Bind | recv_pyobj | `tokenizer_manager.py:314-315` |
| TokenizerManager | PUSH | `ipc:///tmp/xxx` (scheduler_input_ipc) | Bind | send_pyobj | `tokenizer_manager.py:318-319` |
| DPController | PULL | `ipc:///tmp/xxx` (scheduler_input_ipc) | Connect | recv_pyobj | `data_parallel_controller.py:133-134` |
| DPController | PUSH x4 | `ipc:///tmp/xxx` (per-worker) | Bind | send_pyobj | `data_parallel_controller.py:251-256` |
| Scheduler x4 | PULL | `ipc:///tmp/xxx` (per-worker input) | Connect | recv_pyobj | `scheduler.py:460-461` |
| Scheduler x4 | PUSH | `ipc:///tmp/xxx` (tokenizer_ipc) | Connect | send_pyobj | `scheduler.py:467-468` |
| Scheduler x4 | PUSH | `ipc:///tmp/xxx` (detokenizer_ipc) | Connect | send_pyobj | `scheduler.py:477-478` |
| Scheduler x4 | DEALER | `ipc:///tmp/xxx` (rpc_ipc) | Connect | send/recv_pyobj | `scheduler.py:463-464` |
| DetokenizerMgr | PULL | `ipc:///tmp/xxx` (detokenizer_ipc) | Bind | recv_pyobj | `detokenizer_manager.py:96` |
| DetokenizerMgr | PUSH | `ipc:///tmp/xxx` (tokenizer_ipc) | Connect | send_pyobj | `detokenizer_manager.py:99` |
| Engine | DEALER | `ipc:///tmp/xxx` (rpc_ipc) | Bind | send/recv_pyobj | `engine.py:214-215` |

### TCP'ye Geçiş Yapan Konfigürasyonlar

**DP Attention modu** (`--enable-dp-attention`):

```
Tek node: tcp://127.0.0.1:{port+234} ... {port+238}
Multi-node: tcp://{dist_init_addr}:{port+234} ... {port+238}
```

Kaynak: `server_args.py:6541-6591`

**Disaggregation modu** (ek soketler):

| Bileşen | Soket | Bind Adresi | Dosya:Satır |
|---------|-------|-------------|-------------|
| KV Manager | ZMQ PULL | `tcp://{local_ip}:{random}` | `common/conn.py:125-127` |
| Encode Receiver | ZMQ PULL | `tcp://{host_name}:{random}` | `encode_receiver.py:405-407` |
| Encode Server | ZMQ PUSH (on-demand) | connect to receiver | `encode_server.py:1037-1046` |
| Bootstrap Server | HTTP | `0.0.0.0:8998` | `common/conn.py:102` |

**Expert Parallelism** (MoE modeller):

| Bileşen | Soket | Bind Adresi | Dosya:Satır |
|---------|-------|-------------|-------------|
| Backup Manager | ZMQ PULL | `tcp://{local_ip}:{10000+rank*2}` | `expert_backup_manager.py:49-51` |
| Backup Manager | ZMQ PUB | `tcp://{local_ip}:{10001+rank*2}` | `expert_backup_manager.py:53-55` |
| Backup Client | ZMQ SUB | connect to manager PUB | `expert_backup_client.py:57-59` |
| Backup Client | ZMQ PUSH | connect to manager PULL | `expert_backup_client.py:64-66` |

**MessageQueue** (tp>1, multi-node):

| Bileşen | Soket | Bind Adresi | Dosya:Satır |
|---------|-------|-------------|-------------|
| Writer (local) | XPUB | `tcp://127.0.0.1:{random}` | `shm_broadcast.py:204-212` |
| Writer (remote) | XPUB | `tcp://{public_ip}:{random}` | `shm_broadcast.py:224-232` |
| Reader (local) | SUB | connect to 127.0.0.1 | `shm_broadcast.py:273-277` |
| Reader (remote) | SUB | connect to public IP | `shm_broadcast.py:289-296` |

**Dumper** (debug modu):

| Bileşen | Soket | Bind Adresi | Dosya:Satır |
|---------|-------|-------------|-------------|
| HTTP Server | TCP | `0.0.0.0:{port}` | `dumper.py:997` |
| ZMQ RPC | REP | `tcp://*:0` (tüm interface!) | `dumper.py:1044-1046` |

---

## 1.3 Kimlik Doğrulaması (Authentication) Durumu

### Arama Sonuçları

| Mekanizma | Durum | Detay |
|-----------|-------|-------|
| ZMQ CURVE (ECDH key exchange) | **YOK** | Hiçbir sokette `CURVE_SERVERKEY`/`CURVE_PUBLICKEY` yok |
| ZMQ PLAIN Auth | **YOK** | Hiçbir sokette `PLAIN_USERNAME`/`PLAIN_PASSWORD` yok |
| HMAC mesaj imzalama | **YOK** | Gelen pickle verisi imzasız |
| TLS/SSL | **YOK** | ZMQ soketlerinde TLS wrapper yok |
| Token doğrulama | **KISMI** | Sadece disaggregation HTTP endpoint'lerinde, ZMQ'da yok |
| IP whitelist | **YOK** | Bağlanan IP adresi kontrol edilmiyor |

### Mevcut Güvenlik Önlemleri

1. **`get_zmq_socket_on_host()` varsayılan 127.0.0.1** — `network.py:202-203`
   - CVE-2026-3060 kısmi düzeltmesi
   - AMA: Callers `host=self.local_ip` geçerek override edebilir
   - Örnek: `encode_receiver.py:406`, `common/conn.py:126`

2. **SafeUnpickler** — `common.py:2122-2196`
   - Allowlist-based class filtering
   - Deny list: `eval`, `exec`, `compile`, `os.system`, `subprocess.Popen/run`
   - AMA: Sadece `replay_request_dump.py`'de kullanılıyor

---

## 1.4 PortArgs Analizi

### Sınıf Tanımı

Dosya: `python/sglang/srt/server_args.py:6492-6591`

```python
@dataclasses.dataclass
class PortArgs:
    tokenizer_ipc_name: str          # TokenizerMgr ← DetokenizerMgr
    scheduler_input_ipc_name: str    # Scheduler ← TokenizerMgr/DPController
    detokenizer_ipc_name: str        # DetokenizerMgr ← Scheduler
    nccl_port: int                   # torch.distributed init
    rpc_ipc_name: str                # Engine ↔ Scheduler
    metrics_ipc_name: str            # Scheduler → metrics collector
    tokenizer_worker_ipc_name: Optional[str]  # Multi-tokenizer worker
```

### Port Tahsisi Mantığı

```
enable_dp_attention = False (normal mod):
  → Tüm *_ipc_name alanları: ipc:///tmp/{random_tempfile}
  → nccl_port: get_free_port() veya --nccl-port ile belirtilen

enable_dp_attention = True:
  → Tek node (nnodes=1): tcp://127.0.0.1:{port+234..238}
  → Multi-node: tcp://{dist_init_addr}:{offset+1..5}
  → Handshake: tcp://{host}:{port+13}
```

### --dp 4 ile Port Çarpanı

Normal DP modunda (`enable_dp_attention=False`):
- **1 adet** PortArgs oluşturulur (paylaşılan IPC kanalları)
- DPController her worker için ayrı IPC soketi açar (`data_parallel_controller.py:251-256`)
- Toplam IPC soket sayısı: ~6 paylaşılan + 4 worker-specific = ~10 IPC soketi

DP Attention modunda:
- Her DP worker kendi `PortArgs`'ını alır (ayrı TCP portları)
- Toplam TCP port sayısı: ~5 base + dp_size worker = ~9 TCP portu
