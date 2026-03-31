# SGLang ShadowMQ Güvenlik Analizi — Yönetici Özeti

**Tarih:** 2026-03-31
**Analiz Kapsamı:** CVE-2026-3059, CVE-2026-3060, CVE-2026-3989, Orijinal ShadowMQ
**Hedef Deployment:**
```
python -m sglang.launch_server \
  --model-path Qwen3-4B-Thinking-2507 \
  --host 0.0.0.0 --port 30000 \
  --tp 1 --dp 4 --context-length 20480
```
**Sunucu:** Public IP (10.20.20.28), 16 TCP port LISTEN durumunda

---

## Kritik Bulgular Özeti

### 1. Mevcut Konfigürasyonda (tp=1, dp=4, text-only) Durum

| Durum | Açıklama |
|-------|----------|
| **ShadowMQ CVE'leri** | **AKTİF DEĞİL** — Tüm ZMQ kanalları IPC (UNIX domain socket) kullanıyor, TCP'de ZMQ soketi yok |
| **MessageQueue (orijinal ShadowMQ)** | **OLUŞTURULMUYOR** — tp=1 → world_size=1 → `MessageQueue` hiç yaratılmıyor |
| **Pickle deserializasyonu** | IPC kanallarında aktif ama ağdan erişilemez |
| **16 açık port** | HTTP (30000) + NCCL/torch.distributed portları; ZMQ portları değil |
| **Ana risk** | HTTP API'nin 0.0.0.0'da açık olması + NCCL portlarının potansiyel olarak erişilebilir olması |

### 2. CVE Durumu

| CVE | Bileşen | Bu Konfigürasyonda | Genel Risk |
|-----|---------|---------------------|------------|
| **CVE-2026-3059** | Multimodal Gen ZMQ Broker | AKTİF DEĞİL (farklı runtime) | ORTA — localhost binding |
| **CVE-2026-3060** | Encoder Disaggregation | AKTİF DEĞİL (disagg kapalı) | KRİTİK — TCP'de pickle.loads() |
| **CVE-2026-3989** | replay_request_dump.py | AKTİF DEĞİL (utility script) | DÜŞÜK — SafeUnpickler ile korunuyor |
| **ShadowMQ Orijinal** | MessageQueue (shm_broadcast) | AKTİF DEĞİL (tp=1) | KRİTİK — multi-node tp>1'de |

### 3. En Kritik 5 Bulgu

1. **SafeUnpickler mevcut ama kullanılmıyor**: `common.py:2122-2196`'da SafeUnpickler var ama ~30 pickle.loads() noktasının sadece 1'inde kullanılıyor
2. **Sıfır authentication**: Hiçbir ZMQ soketinde HMAC, CURVE, TLS veya token doğrulaması yok
3. **Konfigürasyon değişikliği ile anında kritik hale gelir**: `--enable-dp-attention` veya `--tp 2` eklenmesi tüm IPC kanallarını TCP'ye taşır
4. **Multi-node deployment'larda tam RCE**: TP>1 multi-node'da MessageQueue public IP'de bind eder, unauthenticated pickle ile RCE
5. **PD Disaggregation'da açık kapı**: encode_receiver.py'de her istek için yeni TCP soket açılır, pickle.loads() ile deserialize edilir

### 4. Acil Aksiyon Öğeleri

| Öncelik | Aksiyon | Etki |
|---------|--------|------|
| **P0 — HEMEN** | Port 30000 dışındaki tüm TCP portlarını firewall ile kapatın | NCCL/TCPStore portlarını korur |
| **P0 — HEMEN** | `--host 127.0.0.1` kullanın, reverse proxy arkasına koyun | HTTP API'yi korur |
| **P1 — Bu hafta** | GPU sunucusunu private subnet'e taşıyın | Tüm portları korur |
| **P2 — Bu ay** | Konfigürasyon değişikliği yapmadan önce bu raporu tekrar okuyun | Yeni saldırı yüzeyi açılmasını önler |

### 5. Konfigürasyon Bazlı Risk Haritası (Özet)

```
Risk Yok          Düşük            Orta             Yüksek           KRİTİK
────────────────────────────────────────────────────────────────────────────
LoRA              TP>1             PP>1             EP (MoE)         Multi-node TP>1
Spec. Decoding    (tek node)       Multimodal Gen   Dumper           PD Disaggregation
Chunked Prefill   Multimodal(srt)                                   DP Attention (multi)
Torch Compile     gRPC                                              Multi-node genel
```

---

## Rapor Bölümleri

| # | Dosya | İçerik |
|---|-------|--------|
| 1 | [`01-attack-surface-mapping.md`](01-attack-surface-mapping.md) | Pickle noktaları, ZMQ soketleri, PortArgs analizi |
| 2 | [`02-process-architecture.md`](02-process-architecture.md) | Süreç mimarisi, veri akışı, 16 port analizi |
| 3 | [`03-cve-analysis.md`](03-cve-analysis.md) | CVE-2026-3059, 3060, 3989, ShadowMQ detaylı analiz |
| 4 | [`04-exploit-scenarios.md`](04-exploit-scenarios.md) | Konfigürasyon bazlı exploit senaryoları |
| 5 | [`05-configuration-risk-matrix.md`](05-configuration-risk-matrix.md) | Tüm konfigürasyonlarda risk matrisi |
| 6 | [`06-hardening-guide.md`](06-hardening-guide.md) | CVSS skorları, izleme kuralları, baseline |
| 7 | [`07-patch-recommendations.md`](07-patch-recommendations.md) | Kod yamaları, mimari öneriler |
