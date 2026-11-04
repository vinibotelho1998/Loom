# Loom

Gerenciador de terminal para Linux e Windows, no espírito do Warp, feito para quem
roda vários agentes de linha de comando ao mesmo tempo. Cada aba é um terminal de
verdade, com cor, redimensionamento e Ctrl+C, e cada sessão de agente aparece
numa barra lateral que mostra o estado dela sem você precisar entrar na aba.

[![Build](https://github.com/vinibotelho1998/Loom/actions/workflows/release.yml/badge.svg)](https://github.com/vinibotelho1998/Loom/actions/workflows/release.yml)
![Python](https://img.shields.io/badge/python-3.14-blue)
![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20Windows%20x64-0078D6)
![UI](https://img.shields.io/badge/UI-PySide6%20%2B%20xterm.js-41CD52)
![License](https://img.shields.io/badge/license-MIT-green)

## O que ele faz

**Várias contas logadas ao mesmo tempo.** Cada perfil aponta `CLAUDE_CONFIG_DIR`
para uma pasta própria, então histórico, settings, plugins e MCP de cada conta
ficam separados e nenhuma precisa deslogar para a outra entrar. Abrir uma aba já
na conta escolhida é um clique, e a cor do perfil fica na borda da aba.

**Painel de sessões.** Um terminal comum trata aba como aba. Aqui, aba que roda
um agente é sessão acompanhada: a barra lateral mostra se ela está pensando,
esperando você ou parada, qual foi a última ferramenta chamada e em que arquivo,
quanto de contexto já foi usado e há quanto tempo está assim. Quem está
esperando resposta sobe para o topo, porque é a única que trava o seu tempo.

**Consumo medido, não estimado.** O medidor lê os transcripts em disco, deduplica
por id de mensagem, converte o horário de UTC para o fuso local e agrega por dia,
semana, modelo e perfil. Leitura incremental: guarda o deslocamento de cada
arquivo e nunca relê o que já contou.

**Terminal de verdade.** xterm.js na tela, o PTY do kernel no Linux, pywinpty no
ConPTY do Windows, e a tela fala com os shells pelo canal interno do Qt, sem porta
de rede nenhuma.

## Instalação

**Windows:** baixe o **Loom-Setup.exe** da [última release](https://github.com/vinibotelho1998/Loom/releases/latest)
e execute. Não precisa de Python nem do repositório.

**Linux:** gere o build e coloque no menu do sistema. Testado no KDE Plasma em Wayland.

```
packaging/build.sh
packaging/instalar-linux.sh
```

## Rodar a partir do código-fonte

Precisa de Python 3.14.

```
python -m venv .venv
.venv/bin/pip install -r requirements.txt       no Windows, .venv\Scripts\pip
.venv/bin/python run.py                         no Windows, .venv\Scripts\python
```

## Testes

```
.venv\Scripts\python testes\rodar.py           bateria completa
.venv\Scripts\python testes\rodar.py rapido    pula o que abre processo
```

Os testes de dados rodam cada um numa pasta temporária própria. Os de colar,
seleção e arrastar sobem a janela, que lê a configuração real e abre a aba
padrão com um shell, e o do terminal sobe um shell direto: por isso os quatro
ficam fora do `rapido`.

## Gerar o instalador

No Windows, `BUILD.bat` gera `dist\Loom-Setup.exe`, e o CI gera um a cada push na
`main`. No Linux, `packaging/build.sh` gera a pasta `dist/Loom`.

## Onde ficam os dados

`~/.loom/perfis/<apelido>`, uma pasta por conta. O app nunca lê, copia ou move o
conteúdo do arquivo de credencial de um perfil: ele só aponta a variável de
ambiente para a pasta certa, e o primeiro login de cada perfil é feito pelo
próprio agente dentro da aba, do jeito normal.

## Desenho

O documento de projeto é o [PLANO.md](PLANO.md). Decisão que não estiver lá não
vale.

## Licença

MIT. Veja [LICENSE](LICENSE).

O Loom inclui xterm.js, qwebchannel.js e as fontes Hack e JetBrains Mono, cada um
sob a própria licença. O [NOTICE.md](NOTICE.md) lista todos, e o qwebchannel.js em
particular não é MIT: ele vem do Qt, sob LGPL v3.
