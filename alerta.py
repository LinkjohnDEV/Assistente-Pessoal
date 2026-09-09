"""Avisa no grupo quando o serviço cai. Chamado pelo OnFailure do systemd.

Não usa a IA: se a máquina está em apuros, o último lugar de onde a mensagem
deve depender é de uma chamada de rede pra um modelo.
"""
import logging
import socket
import sys

import assistente
import cerebro


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    motivo = " ".join(sys.argv[1:]) or "falhou"
    try:
        cfg = cerebro.carregar_config(exigir=("whatsapp",))
    except cerebro.ErroLauren as e:
        print(f"não consegui nem ler o config: {e}")
        return 1
    texto = (f"🚨 *A Lauren caiu*\n\n"
             f"O serviço em {socket.gethostname()} {motivo} e não está respondendo.\n"
             f"O systemd tenta subir de novo sozinho. Se esta mensagem se repetir, "
             f"é porque não está conseguindo.")
    return 0 if assistente.enviar_whatsapp(cfg, cfg["whatsapp"]["grupo_jid"], texto) else 1


if __name__ == "__main__":
    sys.exit(main())
