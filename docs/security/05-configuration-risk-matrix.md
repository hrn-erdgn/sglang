# FAZE 6: Tüm Konfigürasyonlarda Risk Matrisi

## 5.1 Konfigürasyon Parametreleri Matrisi

### Ek Saldırı Yüzeyi Tablosu

| Konfigürasyon | Ek TCP Port | Ek Pickle Noktası | Auth | Risk | Yaygınlık | Öncelik |
|---|---|---|---|---|---|---|
| **TP>1 (tek node)** | +1 XPUB 127.0.0.1 | `shm_broadcast.py:453,456` | YOK | DÜŞÜK | ÇOĞUNLUK | DÜŞÜK |
| **TP>1 (multi-node)** | +1 XPUB public IP | `shm_broadcast.py:459` | YOK | KRİTİK | YAYGIN | **EN YÜKSEK** |
| **PP>1 (tek node)** | +0 (torch.dist) | `common.py:1228,1293` (NCCL üzerinden) | YOK | DÜŞÜK | YAYGIN | DÜŞÜK |
| **PP>1 (multi-node)** | +NCCL port | `common.py:1228,1293` (NCCL üzerinden) | YOK | ORTA | YAYGIN | ORTA |
| **EP (MoE)** | +2 TCP/node (10000+) | `expert_backup_manager.py:65`, `client.py:76` | YOK | KRİTİK | NİŞ | YÜKSEK |
| **DP Attention (tek node)** | +5 TCP 127.0.0.1 | Tüm manager recv_pyobj | YOK | DÜŞÜK | YAYGIN | DÜŞÜK |
| **DP Attention (multi-node)** | +5 TCP public | Tüm manager recv_pyobj | YOK | KRİTİK | YAYGIN | **EN YÜKSEK** |
| **PD Disagg (zmq backend)** | +N PULL TCP | `encode_receiver.py:497,735` | YOK | KRİTİK | YAYGIN | **EN YÜKSEK** |
| **PD Disagg (mooncake)** | +RDMA +HTTP 8998 | metadata pickle | YOK | YÜKSEK | YAYGIN | YÜKSEK |
| **PD Disagg (nixl)** | +NIXL ports | struct only (pickle yok) | YOK | ORTA | NİŞ | ORTA |
| **Encoder Parallel** | +N PULL TCP/istek | `encode_receiver.py:497,735` | YOK | KRİTİK | NİŞ | YÜKSEK |
| **Multi-node (genel)** | IPC→TCP | Tüm recv_pyobj TCP'ye geçer | YOK | KRİTİK | YAYGIN | **EN YÜKSEK** |
| **Multimodal (srt VLM)** | +0 | Mevcut IPC | YOK | DÜŞÜK | YAYGIN | DÜŞÜK |
| **Multimodal (multimodal_gen)** | +1 REP 127.0.0.1 | `scheduler_client.py:28` | YOK | ORTA | NİŞ | ORTA |
| **Speculative Decoding** | +0 | +0 | N/A | YOK | YAYGIN | — |
| **LoRA** | +0 | +0 | N/A | YOK | YAYGIN | — |
| **gRPC** | +1 gRPC port | Protobuf (pickle yok) | N/A | DÜŞÜK | NİŞ | DÜŞÜK |
| **Dumper** | +HTTP 0.0.0.0, +ZMQ tcp://*:0 | `dumper.py:1052` | YOK | YÜKSEK | NADİR | ORTA |
| **RDMA/Mooncake** | +RDMA engine | struct (pickle yok) | YOK | ORTA | NİŞ | DÜŞÜK |
| **Chunked Prefill** | +0 | +0 | N/A | YOK | ÇOĞUNLUK | — |
| **Torch Compile** | +0 | +0 | N/A | YOK | YAYGIN | — |
| **Gateway/Router (Rust)** | +HTTP port | JSON (pickle yok) | N/A | DÜŞÜK | NİŞ | DÜŞÜK |

### Yaygınlık Tanımları

| Seviye | Tanım | Örnekler |
|--------|-------|----------|
| **ÇOĞUNLUK** | Hemen herkes kullanıyor | dp>1, tp>1 (tek node), chunked prefill |
| **YAYGIN** | Kurumsal/araştırma ortamlarında sık | Multi-node, PD disagg, DP attention, PP |
| **NİŞ** | Belirli use-case'lerde | EP/MoE, encoder parallel, RDMA, gRPC |
| **NADİR** | Çok az deployment'ta | Dumper, replay scripts, deneysel özellikler |

---

## 5.2 En Tehlikeli Konfigürasyon Kombinasyonları

### #1: Production DeepSeek-V3 Serving

```bash
python -m sglang.launch_server \
  --model-path deepseek-ai/DeepSeek-V3 \
  --tp 8 --dp 4 --ep-size 8 \
  --enable-dp-attention \
  --nnodes 4 --node-rank 0 \
  --dist-init-addr master:29500
```

| Aktif Açık | Dosya | Risk |
|-----------|-------|------|
| MessageQueue remote XPUB | `shm_broadcast.py:224-232,459` | KRİTİK |
| DP Attention TCP kanalları | `server_args.py:6541-6591` | KRİTİK |
| Expert Backup TCP soketleri | `expert_backup_manager.py:49-55` | KRİTİK |
| NCCL inter-node | `model_runner.py:920-922` | YÜKSEK |
| Tüm manager recv_pyobj TCP | `scheduler.py:1413`, `tokenizer_manager.py:1524` | KRİTİK |

**Toplam açık TCP pickle noktası: ~15+**
**Öngörülebilir portlar: Evet (sabit offset'ler)**
**Genel risk: EN YÜKSEK — Tam RCE zinciri mümkün**

### #2: Multimodal API Servisi (VLM + PD Disagg)

```bash
# Prefill node
python -m sglang.launch_server \
  --model-path Qwen/Qwen2-VL-72B-Instruct \
  --tp 4 --disaggregation-mode prefill \
  --encoder-transfer-backend zmq_to_scheduler

# Decode node
python -m sglang.launch_server \
  --model-path Qwen/Qwen2-VL-72B-Instruct \
  --tp 4 --disaggregation-mode decode
```

| Aktif Açık | Dosya | Risk |
|-----------|-------|------|
| Encode receiver pickle | `encode_receiver.py:497,735` | KRİTİK |
| KV Manager ZMQ PULL | `common/conn.py:125-127` | KRİTİK |
| Bootstrap HTTP (8998) | `common/conn.py:102` | YÜKSEK |
| MessageQueue (tp=4) | `shm_broadcast.py:453,456` | DÜŞÜK (tek node) |

**Toplam: 3 KRİTİK + 1 YÜKSEK**

### #3: Araştırma Lab'ı (Single-node)

```bash
python -m sglang.launch_server \
  --model-path meta-llama/Llama-3-70B \
  --tp 4 --dp 2
```

| Aktif Açık | Dosya | Risk |
|-----------|-------|------|
| MessageQueue local XPUB | `shm_broadcast.py:204-212,453,456` | DÜŞÜK (localhost) |
| NCCL port | `model_runner.py:920` | DÜŞÜK (localhost) |

**Genel risk: DÜŞÜK — Tüm soketler localhost'ta**

### #4: Kullanıcının Konfigürasyonu (tp=1, dp=4)

```bash
python -m sglang.launch_server \
  --model-path Qwen3-4B-Thinking-2507 \
  --host 0.0.0.0 --port 30000 \
  --tp 1 --dp 4 --context-length 20480
```

| Aktif Açık | Risk |
|-----------|------|
| HTTP API 0.0.0.0:30000 | ORTA |
| NCCL portları (4x) | DÜŞÜK (muhtemelen localhost) |
| ShadowMQ CVE'leri | YOK (hiçbiri aktif değil) |

**Genel risk: DÜŞÜK-ORTA — Ana risk HTTP API'nin açık olması**

---

## 5.3 Konfigürasyon Değişikliği Etki Analizi

Kullanıcının mevcut konfigürasyonuna tek parametre eklendiğinde:

| Eklenen Parametre | Yeni Risk Seviyesi | Açılan Saldırı Yüzeyi |
|---|---|---|
| `--tp 2` | DÜŞÜK | MessageQueue local XPUB (127.0.0.1) |
| `--tp 2 --nnodes 2` | **KRİTİK** | MessageQueue remote XPUB (public IP) + NCCL |
| `--enable-dp-attention` | DÜŞÜK | 5 TCP port (127.0.0.1, tek node) |
| `--enable-dp-attention --nnodes 2` | **KRİTİK** | 5+ TCP port (public IP) + handshake |
| `--ep-size 4` (MoE model ile) | **KRİTİK** | Expert backup TCP (public IP, sabit port) |
| `--disaggregation-mode prefill` | **KRİTİK** | encode_receiver PULL + bootstrap HTTP |
| `--encoder-transfer-backend zmq_to_scheduler` | **KRİTİK** | pickle.loads() TCP üzerinde |
| `DUMPER_ENABLE=1` | YÜKSEK | HTTP 0.0.0.0 + ZMQ tcp://*:0 |
| `--pp 2` | DÜŞÜK | torch.dist üzerinden pickle (lokal) |
| `--pp 2 --nnodes 2` | ORTA | torch.dist TCP üzerinden |
| `--speculative-algorithm eagle` | YOK | Ek soket açılmaz |
| `--lora-paths ...` | YOK | Ek soket açılmaz |
| `--chunked-prefill-size 8192` | YOK | Ek soket açılmaz |
