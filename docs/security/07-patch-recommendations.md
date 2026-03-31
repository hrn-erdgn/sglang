# Yama Önerileri

## 7.1 Öncelik Sırası

| # | Yama | Etki | Karmaşıklık | Geriye Uyumluluk |
|---|------|------|-------------|------------------|
| 1 | SafeUnpickler'ı tüm pickle.loads noktalarına uygula | KRİTİK | DÜŞÜK | TAM |
| 2 | ZMQ soketlerini localhost'a bind et (varsayılan) | YÜKSEK | DÜŞÜK | KISMI |
| 3 | ZMQ CURVE authentication ekle | KRİTİK | ORTA | TAM |
| 4 | recv_pyobj → safe_recv_pyobj wrapper | KRİTİK | DÜŞÜK | TAM |
| 5 | TCP kanallarında pickle → msgpack geçişi | YÜKSEK | YÜKSEK | KIRILMA |
| 6 | NCCL/TCPStore bind adresini kısıtla | ORTA | DÜŞÜK | TAM |

---

## 7.2 Yama 1: SafeUnpickler'ı Tüm Pickle Noktalarına Uygula

### Mevcut Durum

SafeUnpickler `common.py:2122-2196`'da tanımlı ama sadece `replay_request_dump.py`'de kullanılıyor.

### Değişiklik Gereken Dosyalar

```
python/sglang/srt/disaggregation/encode_receiver.py       (satır 497, 735)
python/sglang/srt/distributed/device_communicators/shm_broadcast.py (satır 453, 456, 459)
python/sglang/multimodal_gen/runtime/scheduler_client.py   (satır 28)
python/sglang/srt/distributed/parallel_state.py            (satır 1155)
python/sglang/srt/distributed/utils.py                     (satır 187, 207)
python/sglang/srt/distributed/naive_distributed.py         (satır 87)
python/sglang/srt/utils/common.py                          (satır 1228, 1293)
python/sglang/srt/managers/multi_tokenizer_mixin.py        (satır 462)
```

### Örnek Yama — encode_receiver.py

```python
# ÖNCE (encode_receiver.py:497):
recv_obj: EmbeddingData = pickle.loads(parts[0])

# SONRA:
from sglang.srt.utils.common import SafeUnpickler
import io

recv_obj: EmbeddingData = SafeUnpickler(io.BytesIO(bytes(parts[0]))).load()
```

### Örnek Yama — shm_broadcast.py

```python
# ÖNCE (shm_broadcast.py:453):
obj = pickle.loads(buf[1:])

# SONRA:
from sglang.srt.utils.common import SafeUnpickler
import io

obj = SafeUnpickler(io.BytesIO(bytes(buf[1:]))).load()
```

### SafeUnpickler İyileştirmeleri

Mevcut SafeUnpickler'ın allowlist'i çok geniş. Önerilen kısıtlamalar:

```python
# common.py — SafeUnpickler iyileştirmesi
class SafeUnpickler(pickle.Unpickler):
    ALLOWED_MODULE_PREFIXES = {
        # Mevcut liste + daha kısıtlayıcı
        # "builtins." → sadece güvenli builtins
        "builtins.dict",
        "builtins.list",
        "builtins.tuple",
        "builtins.set",
        "builtins.frozenset",
        "builtins.bytes",
        "builtins.bytearray",
        "builtins.str",
        "builtins.int",
        "builtins.float",
        "builtins.bool",
        "builtins.complex",
        "builtins.slice",
        "builtins.range",
        "builtins.type",
        # "types." → kısıtla (CodeType, FunctionType tehlikeli)
        # Bu prefix'i kaldır, zaten DENY_CLASSES'ta yakalanıyor ama
        # prefix match daha önce çalışıyor
    }

    DENY_CLASSES = {
        # Mevcut liste + eklemeler:
        ("builtins", "eval"),
        ("builtins", "exec"),
        ("builtins", "compile"),
        ("builtins", "getattr"),   # ← EKLENMELİ (gadget chain)
        ("builtins", "__import__"), # ← EKLENMELİ
        ("os", "system"),
        ("os", "popen"),           # ← EKLENMELİ
        ("os", "execve"),          # ← EKLENMELİ
        ("subprocess", "Popen"),
        ("subprocess", "run"),
        ("subprocess", "call"),    # ← EKLENMELİ
        ("subprocess", "check_output"), # ← EKLENMELİ
        ("codecs", "decode"),
        ("types", "CodeType"),
        ("types", "FunctionType"),
        ("importlib", "import_module"), # ← EKLENMELİ
        ("webbrowser", "open"),    # ← EKLENMELİ
        ("ctypes", "CDLL"),        # ← EKLENMELİ (native code exec)
        ("ctypes", "cdll"),        # ← EKLENMELİ
    }
```

---

## 7.3 Yama 2: safe_recv_pyobj Wrapper

### Sorun

ZMQ'nun `recv_pyobj()` metodu dahili olarak `pickle.loads()` kullanır ve override edilemez.

### Çözüm — Güvenli wrapper

```python
# utils/network.py'ye eklenecek:
import io
from sglang.srt.utils.common import SafeUnpickler

def safe_recv_pyobj(socket, flags=0):
    """recv_pyobj replacement that uses SafeUnpickler."""
    msg = socket.recv(flags)
    return SafeUnpickler(io.BytesIO(msg)).load()

def safe_send_pyobj(socket, obj, flags=0, protocol=pickle.DEFAULT_PROTOCOL):
    """send_pyobj replacement (serialization side — same as original)."""
    msg = pickle.dumps(obj, protocol)
    return socket.send(msg, flags)
```

### Uygulama

Tüm `recv_pyobj()` çağrılarını `safe_recv_pyobj()` ile değiştir:

```python
# ÖNCE (scheduler.py:1413):
recv_req = self.recv_from_tokenizer.recv_pyobj(zmq.NOBLOCK)

# SONRA:
from sglang.srt.utils.network import safe_recv_pyobj
recv_req = safe_recv_pyobj(self.recv_from_tokenizer, zmq.NOBLOCK)
```

Etkilenen dosya sayısı: ~15 dosya, ~60 çağrı noktası.

---

## 7.4 Yama 3: ZMQ CURVE Authentication

### Konsept

ZMQ CURVE, ECDH key exchange ile soketler arası mutual authentication sağlar.

```python
# Server tarafı (bind):
import zmq.auth
from zmq.auth.thread import ThreadAuthenticator

auth = ThreadAuthenticator(context)
auth.start()
auth.allow('10.20.20.0/24')  # IP whitelist
auth.configure_curve(domain='*', location=zmq.auth.CURVE_ALLOW_ANY)

server_socket.curve_secretkey = server_secret
server_socket.curve_publickey = server_public
server_socket.curve_server = True

# Client tarafı (connect):
client_socket.curve_secretkey = client_secret
client_socket.curve_publickey = client_public
client_socket.curve_serverkey = server_public
```

### Uygulama Noktaları

`get_zmq_socket()` ve `get_zmq_socket_on_host()` fonksiyonlarına opsiyonel CURVE parametresi:

```python
# network.py — önerilen değişiklik:
def get_zmq_socket_on_host(
    context, socket_type, host=None,
    curve_keys=None,  # ← YENİ: (server_public, server_secret) tuple
):
    socket = context.socket(socket_type)
    config_socket(socket, socket_type)

    if curve_keys is not None:
        server_public, server_secret = curve_keys
        socket.curve_secretkey = server_secret
        socket.curve_publickey = server_public
        socket.curve_server = True

    # ... mevcut bind mantığı
```

### Key Dağıtımı

```bash
# Başlangıçta key çifti oluştur:
python -c "
import zmq.auth
server_public, server_secret = zmq.auth.create_certificates('/tmp/zmq_keys', 'server')
client_public, client_secret = zmq.auth.create_certificates('/tmp/zmq_keys', 'client')
"

# Çevre değişkenleri ile dağıt:
export SGLANG_ZMQ_SERVER_KEY=/tmp/zmq_keys/server.key_secret
export SGLANG_ZMQ_CLIENT_KEY=/tmp/zmq_keys/client.key_secret
```

---

## 7.5 Yama 4: Pickle → Msgpack/Protobuf Geçişi (Uzun Vadeli)

### Neden Pickle Tehlikeli?

Pickle, Python objelerini serialize/deserialize ederken **arbitrary code execution** sağlar. `__reduce__()` metodu ile herhangi bir callable çağrılabilir. Bu, pickle'ın tasarım gereği bir özelliğidir ve "düzeltilemez" — sadece kısıtlanabilir.

### Alternatif Seçenekler

| Format | Hız | Güvenlik | Python Obje Desteği | Karmaşıklık |
|--------|-----|----------|---------------------|-------------|
| **pickle** | En hızlı | Yok | Tam | Sıfır (mevcut) |
| **msgpack** | Hızlı | Evet | Kısıtlı (dict/list/scalar) | Düşük |
| **protobuf** | Orta | Evet | Schema-based | Yüksek |
| **flatbuffers** | En hızlı (zero-copy) | Evet | Schema-based | Yüksek |
| **JSON** | Yavaş | Evet | Kısıtlı | Düşük |

### Önerilen Yaklaşım

**Aşamalı geçiş:**
1. **Faz 1:** TCP kanallarında `safe_recv_pyobj()` ile SafeUnpickler (hemen)
2. **Faz 2:** Yeni disaggregation mesajlarını msgpack ile (orta vadeli)
3. **Faz 3:** Core manager kanallarını yapılandırılmış formata geçir (uzun vadeli)

```python
# Faz 2 örneği — encode_receiver.py için msgpack:
import msgpack

# Serialization (encode_server.py):
serialized = msgpack.packb({
    "req_id": mm_data.req_id,
    "grid_dim": mm_data.grid_dim,
    "shape": list(mm_data.shape),
    "dtype": str(mm_data.dtype),
    "error_msg": mm_data.error_msg,
})

# Deserialization (encode_receiver.py):
data = msgpack.unpackb(parts[0])
recv_obj = EmbeddingData(**data)
```

---

## 7.6 Yama 5: Bind Adresi Kısıtlama

### MessageQueue (shm_broadcast.py)

```python
# ÖNCE (satır 188-191):
if connect_ip is None:
    connect_ip = (
        get_local_ip_auto("0.0.0.0") if n_remote_reader > 0 else "127.0.0.1"
    )

# SONRA:
if connect_ip is None:
    # Varsayılan olarak 127.0.0.1 kullan
    # Multi-node için explicit IP geçilmeli
    connect_ip = "127.0.0.1"
    if n_remote_reader > 0:
        logger.warning(
            "MessageQueue has remote readers but no explicit connect_ip. "
            "Defaulting to 127.0.0.1. Set connect_ip explicitly for multi-node."
        )
```

### Expert Backup Manager

```python
# ÖNCE (satır 50-55):
self.recv_from_expert_backup_client.bind(
    f"tcp://{get_local_ip_auto()}:{PORT_BASE + server_args.node_rank * 2}"
)

# SONRA — explicit IP parametresi veya private interface:
bind_ip = server_args.dist_init_addr or "127.0.0.1"
self.recv_from_expert_backup_client.bind(
    f"tcp://{bind_ip}:{PORT_BASE + server_args.node_rank * 2}"
)
```

### NCCL/torch.distributed

```python
# ÖNCE (model_runner.py:920-922):
dist_init_method = NetworkAddress(
    self.server_args.host or "127.0.0.1", self.dist_port
).to_tcp()

# Bu zaten 127.0.0.1 varsayıyor AMA --host 0.0.0.0 ile 0.0.0.0 olur
# SONRA — her zaman 127.0.0.1 kullan (tek node):
if self.server_args.nnodes == 1:
    dist_host = "127.0.0.1"
else:
    dist_host = self.server_args.dist_init_addr or self.server_args.host or "127.0.0.1"
dist_init_method = NetworkAddress(dist_host, self.dist_port).to_tcp()
```

---

## 7.7 Özet — Maintainer'lara Gönderilecek Yama Paketi

### Dosya Değişiklikleri

| Dosya | Değişiklik Tipi | Yama # |
|-------|----------------|--------|
| `srt/utils/common.py` | SafeUnpickler DENY_CLASSES genişlet | 1 |
| `srt/utils/network.py` | `safe_recv_pyobj()` ekle, CURVE desteği | 2, 3 |
| `srt/disaggregation/encode_receiver.py` | pickle.loads → SafeUnpickler | 1 |
| `srt/distributed/device_communicators/shm_broadcast.py` | pickle.loads → SafeUnpickler, bind kısıtla | 1, 5 |
| `multimodal_gen/runtime/scheduler_client.py` | pickle.loads → SafeUnpickler | 1 |
| `srt/managers/scheduler.py` | recv_pyobj → safe_recv_pyobj | 2 |
| `srt/managers/tokenizer_manager.py` | recv_pyobj → safe_recv_pyobj | 2 |
| `srt/managers/detokenizer_manager.py` | recv_pyobj → safe_recv_pyobj | 2 |
| `srt/managers/data_parallel_controller.py` | recv_pyobj → safe_recv_pyobj | 2 |
| `srt/elastic_ep/expert_backup_manager.py` | recv_pyobj → safe_recv_pyobj, bind kısıtla | 2, 5 |
| `srt/elastic_ep/expert_backup_client.py` | recv_pyobj → safe_recv_pyobj | 2 |
| `srt/debug_utils/dumper.py` | recv_pyobj → safe_recv_pyobj | 2 |
| `srt/model_executor/model_runner.py` | dist_init 127.0.0.1 zorla (tek node) | 5 |
| `srt/distributed/parallel_state.py` | pickle.loads → SafeUnpickler | 1 |
| `srt/distributed/utils.py` | pickle.loads → SafeUnpickler | 1 |

### Geriye Dönük Uyumluluk

- **Yama 1-2 (SafeUnpickler/safe_recv_pyobj):** %100 uyumlu — aynı pickle formatı, ek filtreleme
- **Yama 3 (CURVE):** Opsiyonel — env var ile aktifleştirme, varsayılan kapalı
- **Yama 4 (msgpack):** Kırılma değişikliği — encoder/receiver sürüm uyumu gerekir
- **Yama 5 (bind kısıtlama):** Bazı multi-node konfigürasyonlarda ek parametre gerekebilir
