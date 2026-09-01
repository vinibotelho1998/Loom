# DBX Term

Gerenciador de console proprio, no espirito do Warp, feito pra rodar Claude Code e outros CLI.
Duas coisas que nenhum terminal de mercado faz hoje e sao o motivo do projeto:

1. manter varias contas do Claude Code logadas ao mesmo tempo e trocar entre elas em um clique
2. mostrar o consumo real de cada conta, por dia e por semana, com numero medido e nao estimado

Este arquivo e o mestre do projeto. Decisao que nao estiver aqui nao vale.

---

## 1. Stack

Python 3.14 em venv, PySide6 pra janela, QWebEngineView pra tela, xterm.js pro terminal, pywinpty pro ConPTY.

Por que assim:

- xterm.js e o emulador de terminal do VS Code. Escrever emulador ANSI do zero e trabalho de meses e da bug pra sempre. Ele entra como arquivo estatico baixado uma vez, sem npm e sem Node.
- pywinpty da o ConPTY do Windows, que e a unica forma de rodar um shell de verdade com cor, redimensionamento e Ctrl+C.
- PySide6 e a stack do DBX Wire, entao a casa ja e conhecida.
- Python 3.14, o que ja esta na maquina. Confirmado em 01/09/2026 que PySide6 6.11.2 e pywinpty 3.0.5 sao instalaveis nele, entao nao precisa de segundo Python.

Descartado Electron. Exigiria instalar Node so pra isso e o ganho seria zero.

---

## 2. Arquitetura

Um processo Python. Dentro dele:

- **Nucleo de sessao.** Abre um ConPTY por aba, guarda o processo filho, faz o repasse de bytes nos dois sentidos e trata o resize.
- **Ponte.** WebSocket em localhost com porta efemera e token por execucao. Liga cada aba da tela ao seu PTY. Nada escuta fora da maquina.
- **Tela.** HTML e JS servidos do disco local dentro do QWebEngineView. Cada aba e uma instancia do xterm.js.
- **Perfis.** Cadastro das contas, cada uma com sua pasta de config isolada.
- **Medidor.** Le os transcript em disco e devolve o consumo agregado.

A janela e o cromo. O terminal e a web. O sistema operacional fica todo do lado Python.

---

## 3. Contas do Claude Code

Confirmado na maquina em 01/09/2026:

- o binario aceita a variavel de ambiente `CLAUDE_CONFIG_DIR`
- a credencial fica em arquivo comum, `~/.claude/.credentials.json`, e nao no Credential Manager do Windows

Entao cada conta vira uma pasta propria de config, por exemplo `~/.dbx-term/perfis/trabalho` e `~/.dbx-term/perfis/pessoal`.
Abrir uma aba naquela conta e so exportar `CLAUDE_CONFIG_DIR` apontando pra pasta dela antes de subir o shell.

Consequencias boas de graca:

- as duas contas ficam logadas ao mesmo tempo, sem logout e sem relogin
- da pra rodar as duas lado a lado, em abas diferentes, na mesma janela
- historico, settings, plugins e MCP de cada conta ficam separados e nao se misturam

Regras que o app segue:

- o app nunca le, copia ou move o conteudo do arquivo de credencial. Ele so aponta a variavel pra pasta certa.
- o primeiro login de um perfil e feito pelo proprio `claude` dentro da aba, do jeito normal.
- perfil tem apelido, cor e plano anotado por voce. O app nao inventa nem adivinha.

---

## 4. Medidor de uso

Fonte: `PERFIL/projects/**/*.jsonl`. Cada resposta gravada traz o consumo completo daquela chamada.

Campos aproveitados por mensagem: modelo, horario, input, output, thinking, cache escrito, cache lido.

Regras do medidor:

- deduplicar por id da mensagem, senao retomada de sessao conta duas vezes
- converter o horario, que vem em UTC, pro fuso local antes de fechar o dia
- agregar por dia, por semana, por modelo e por perfil
- ler so o que mudou desde a ultima leitura, guardando o deslocamento de cada arquivo, igual o statusline ja faz

O que o medidor **nao** faz: mostrar a barra oficial do plano, aquele percentual de limite semanal do `/usage`.
Esse numero vem do servidor da Anthropic e nao existe em disco. A tela mostra consumo medido e diz isso com todas as letras,
pra ninguem confundir uma coisa com a outra.

Aferido em 01/09/2026 na conta atual, so pra provar que a leitura funciona:
semana corrente com 484 mensagens, 503 mil de saida sendo 167 mil de raciocinio, 4,1 milhoes de cache escrito e 282 milhoes de cache lido.

---

## 5. Tela de contas

Uma linha por conta, com:

- apelido, cor e plano
- gasto de hoje e da semana, quebrado por modelo
- numero de mensagens e de sessoes
- quando foi o ultimo uso
- botao de abrir aba nova ja naquela conta

Grafico simples de barra por dia nos ultimos trinta dias, empilhado por modelo.

---

## 6. Painel de sessoes

O terminal comum trata aba como aba. Aqui aba que roda Claude Code e uma sessao acompanhada.

Uma barra lateral lista tudo que esta aberto e, pra cada sessao de Claude, mostra sem voce precisar entrar nela:

- **Estado.** Tres estados que importam: pensando, esperando voce, parada. Quem esta esperando resposta sobe pro topo e pisca discreto, porque e a unica que trava o teu tempo.
- **No que esta.** Ultima ferramenta chamada e em que arquivo, tipo "editando itbi.xml" ou "rodando maven". E o que o transcript ja grava.
- **Andamento.** Lista de tarefas da sessao, quantas fechadas de quantas, quando existir.
- **Peso.** Quanto de contexto ja foi usado naquela sessao e quanto ela ja gastou.
- **Conta e pasta.** Qual perfil e qual repositorio, com a cor do perfil na borda.
- **Tempo.** Ha quanto tempo esta assim. Sessao parada ha muito tempo aparece apagada.

Como isso e lido, sem gambiarra e sem falar com servidor:

- o transcript da sessao e escrito em disco durante a conversa, entao acompanhar o fim do arquivo da o estado quase ao vivo
- o processo filho de cada aba e nosso, entao sabemos se o `claude` ainda esta de pe
- fim de arquivo em resposta do modelo sem chamada de ferramenta pendente quer dizer esperando voce
- so ler o que cresceu desde a ultima leitura, nunca reler o arquivo inteiro

Fecha o circulo: clicou na sessao, vai pra aba dela. Alt e numero pula direto. E quando uma sessao passa pra esperando voce
com a janela em segundo plano, notificacao do Windows.

## 7. Visual

O app tem que ser bonito de olhar oito horas por dia, e bonito aqui quer dizer calmo, nao enfeitado.

- **Base escura de verdade.** Fundo quase preto com leve tom frio, texto em cinza claro e nao branco puro, pra nao cansar. Tema claro existe e e igualmente cuidado.
- **Cor com significado.** Cor nao decora, ela informa. Cada perfil tem a sua e ela aparece na borda da aba, no ponto da sessao e no grafico de uso. Fora isso, a tela e monocromatica. Vermelho fica reservado pra erro e pra mais nada.
- **Uma fonte de codigo com ligadura**, tamanho e entrelinha ajustaveis, porque saida de terminal e leitura longa.
- **Silencio visual.** Sem borda em tudo, sem sombra em tudo. Separacao por espaco e por tom de fundo. Barra de titulo propria, fina, integrada.
- **Movimento curto.** Transicao de 120 a 180 milissegundos em abrir aba, trocar de aba e abrir painel. Nada que voce espere terminar.
- **Densidade a sua escolha.** Modo compacto e modo confortavel, porque a lateral com dez sessoes e a lateral com duas pedem coisas diferentes.
- **Grafico limpo.** Barra por dia, empilhada por modelo, sem grade pesada, sem legenda gritando, numero grande so no total.

A referencia de acabamento e o Warp e o Ghostty. O que nao vamos copiar e o excesso de painel do Warp,
que rouba espaco do que interessa, que e a saida do comando.

---

## 8. Terminal, o que precisa ser bom

O que costuma ser ruim em terminal no Windows e aqui tem que funcionar:

- **Colar.** Colar varias linhas nao pode disparar comando a cada quebra. O app usa colagem entre marcas, o bracketed paste, e ainda avisa antes de colar bloco grande. Colar imagem e caminho de arquivo tambem.
- **Aba e painel.** Ctrl+T abre aba, Ctrl+W fecha, Ctrl+Tab navega. Divisao de painel fica pra depois.
- **Busca na saida.** Ctrl+F busca no que ja rolou.
- **Rolagem longa.** Historico grande sem travar, usando a renderizacao por WebGL do xterm.js.
- **Link clicavel.** Caminho de arquivo com linha abre no editor, que e o formato que o Claude Code ja imprime.

Bloco por comando, no estilo Warp, fica fora da primeira versao. Depende de marcar o inicio e o fim de cada comando no shell,
o que exige mexer no prompt do PowerShell, e nao vale o risco agora.

---

## 9. Etapas

Cada etapa fecha rodando na tua maquina, nao no papel.

**Etapa 0. Base que roda.** Janela abre, uma aba, PowerShell dentro dela, digitar e ver saida, redimensionar, fechar limpo.
Sem perfil, sem uso, sem enfeite. So provar que o ConPTY e o xterm.js conversam.

**Etapa 1. Muitas abas e perfis.** Varias abas ao mesmo tempo, cadastro de conta, pasta isolada por conta,
aba abre na conta escolhida e mostra na borda de qual conta ela e.

**Etapa 2. Painel de sessoes.** Barra lateral com o estado de cada sessao de Claude, no que ela esta,
ha quanto tempo, e quem esta esperando voce. E o coracao do projeto e vem antes do medidor de proposito,
porque e o que muda o teu dia.

**Etapa 3. Medidor e tela de contas.** Leitura dos transcript, agregacao por dia, semana, modelo e perfil,
mais a tela de contas com grafico.

**Etapa 4. Visual.** Tema escuro e claro, tipografia, densidade, movimento, barra de titulo propria.
Vem depois porque so faz sentido pintar quando o esqueleto ja esta de pe, e antes disso a gente estaria pintando chute.

**Etapa 5. Conforto.** Colar direito, busca na saida, atalhos, link clicavel, notificacao do Windows.

**Etapa 6. Acabamento.** Restaurar sessao ao abrir, exportar o uso, atalho global.

## 10. Riscos

- **Peso do QWebEngine.** Passa de 150 MB instalado. Aceito, ja que o ganho e nao escrever emulador.
- **Formato do transcript.** E arquivo interno do Claude Code e pode mudar sem aviso. O medidor le de forma tolerante e ignora linha que nao reconhece, em vez de quebrar.
- **Ponte local.** Porta so em localhost, porta efemera e token por execucao. Sem isso qualquer pagina aberta no navegador falaria com o teu terminal.
