"""Ponto de entrada do Loom.

Sem argumento sobe o nucleo, que e dono dos shells e sobe a tela como outro processo, com --tela.
Assim a tela pode cair sem levar os shells junto. LOOM_SEM_NUCLEO volta ao jeito de antes, com
tudo num processo so, pra quando o nucleo der problema.

O Chromium e o Qt Quick leem o motor de desenho das variaveis de ambiente uma vez so, quando
sobem. Por isso ele e escolhido aqui, antes de qualquer import do Qt, e nao dentro do app. O
log vem antes de tudo, pra anotar a escolha e o que der errado dali em diante.
"""
import os
import sys

from loom import config as mod_config
from loom import motor as mod_motor
from loom import registro as mod_registro

mod_registro.configurar()

if "--tela" not in sys.argv and not os.environ.get("LOOM_SEM_NUCLEO"):
    from loom.nucleo import main_nucleo

    raise SystemExit(main_nucleo())

# Sem teto de quadros e sem as flags de antes: com a placa de video o Qt sobe no padrao dele, e
# depois de uma queda da tela sobe por software. O porque mora no loom/motor.py.
mod_motor.preparar_motor(os.environ, mod_config.carregar())

from loom.app import main  # noqa: E402

raise SystemExit(main())
