# Terceiros

O Loom é distribuído sob a [licença MIT](LICENSE). Este arquivo lista o que ele
inclui de terceiros, cada um sob a própria licença, que continua valendo.

## Versionado neste repositório

### xterm.js e addons
`ui/vendor/xterm.js`, `ui/vendor/xterm.css`, `ui/vendor/addon-fit.js`,
`ui/vendor/addon-search.js`, `ui/vendor/addon-webgl.js`, `ui/vendor/addon-weblinks.js`

Copyright (c) 2014 The xterm.js authors
Copyright (c) 2012-2013 Christopher Jeffrey

Licença MIT. https://github.com/xtermjs/xterm.js

`ui/vendor/addon-webgl.js` tem uma modificação do Loom, marcada no topo do arquivo. A textura de
letras passa a usar filtro linear e o addon deixa de chamar `generateMipmap` a cada envio dela. Sem
filtro próprio, o WebGL usa o padrão, que exige mipmaps; com a Radeon RX 9070 no Mesa, o driver
recusava gerar os mipmaps, com `GL_INVALID_OPERATION` em `allocateMipmapLevelsForGeneration`, a textura
ficava incompleta e as letras novas saíam como blocos pretos. O terminal desenha na escala de um
para um, então a imagem não muda. `testes/test_desenho_webgl.py` confere a modificação, para ela não
sumir numa atualização do xterm.js.

### Qt WebChannel, lado JavaScript
`ui/vendor/qwebchannel.js`

Copyright (C) 2016 The Qt Company Ltd.
Copyright (C) 2016 Klarälvdalens Datakonsult AB, a KDAB Group company

`SPDX-License-Identifier: LicenseRef-Qt-Commercial OR LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`

Este arquivo **não** é MIT. Ele vem do Qt e segue as licenças acima. Aqui ele é
usado sob a LGPL v3, sem modificação, exatamente como distribuído pelo Qt.
https://doc.qt.io/qt-6/qtwebchannel-index.html

### CodexBar, desenho e regras do painel de uso
`ui/painel-uso.js`, `ui/painel-uso.css`

O painel de uso reproduz o desenho, os textos e as regras de ritmo, projeção e formatação
do CodexBar, e usa os ícones de provedor dele.

Copyright (c) 2026 Peter Steinberger, licença MIT. https://github.com/steipete/CodexBar

### Fonte Hack
`ui/vendor/fonts/hack-regular.woff2`, `hack-bold.woff2`, `hack-italic.woff2`

Copyright (c) 2018 Source Foundry Authors, licença MIT.
Contém partes derivadas do Bitstream Vera Sans Mono, Copyright (c) 2003 Bitstream Inc.,
sob a Bitstream Vera License. https://github.com/source-foundry/Hack

### Fonte JetBrains Mono
`ui/vendor/fonts/jetbrains-mono-latin-400-normal.woff2`,
`jetbrains-mono-latin-700-normal.woff2`, `jetbrains-mono-latin-ext-400-normal.woff2`

Copyright (c) 2020 The JetBrains Mono Project Authors, sob a
SIL Open Font License 1.1. https://github.com/JetBrains/JetBrainsMono

## Código e desenho de terceiros

Os dados do painel de uso foram escritos a partir de projetos abertos. Nenhum arquivo
deles foi copiado inteiro; o que veio de cada um está descrito abaixo.

### CodexBar
`loom/painel_uso.py`

O relatório que alimenta o painel de uso segue o layout do CodexBar: janela de sessão e
semanal, bloco com custo e ritmo, custo de hoje e dos últimos 30 dias e série por dia, e
a mesma divisão entre o que chega pronto e o que a tela calcula.

Copyright (c) 2026 Peter Steinberger, licença MIT. https://github.com/steipete/CodexBar

### ccusage
`loom/uso.py`, `loom/uso_codex.py`, `loom/painel_uso.py`

As regras de uso, bloco e custo vêm do ccusage: a resposta repetida reconhecida pelo id
da mensagem mais o do pedido, o bloco de 5 horas que começa na hora cheia e fecha depois
de 5 horas ou de 5 horas sem uso, o ritmo e a projeção do bloco e o custo por token com o
cache escrito e o lido.

Copyright (c) ryoppippi, licença MIT. https://github.com/ryoppippi/ccusage

### LiteLLM
`loom/dados/precos.json`

A tabela de preço por token é um recorte da `model_prices_and_context_window.json` do
LiteLLM, baixada em 28/09/2026: só os modelos do Claude e da OpenAI que o Loom lê e só
os campos de custo, com os valores como vieram. A faixa de contexto longo segue a regra
do LiteLLM.

Copyright (c) 2023 Berri AI, licença MIT. https://github.com/BerriAI/litellm

## Empacotado no instalador

O `Loom-Setup.exe` é gerado com PyInstaller e leva junto:

- **Qt 6 e PySide6**, The Qt Company, sob LGPL v3. O Qt entra como biblioteca
  dinâmica, sem modificação, e pode ser substituído por outra build compatível.
  https://www.qt.io/licensing
- **pywinpty**, licença MIT, que embute o **winpty** de Ryan Prichard, também MIT.
  https://github.com/andfoy/pywinpty
- **Microsoft Visual C++ Redistributable**, redistribuível sob os termos da
  Microsoft. Ele é apenas baixado e executado pelo instalador, não é modificado.

## Como conferir

Os arquivos de terceiros ficam todos em `ui/vendor/`. Só um deles foi editado, o
`ui/vendor/addon-webgl.js`, com a modificação descrita acima e marcada no topo dele; os
outros são cópias do que o projeto de origem publica. A outra exceção é
`loom/dados/precos.json`, o recorte da tabela do LiteLLM descrito acima.
