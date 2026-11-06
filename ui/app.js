"use strict";


const TEMA = {
  background: "#111214",
  foreground: "#e6e6e8",
  cursor: "#2ab4e0",
  cursorAccent: "#111214",
  // com o fundo escuro, o azul antigo quase nao aparecia por cima do texto
  selectionBackground: "#2f5a6b",
  selectionForeground: "#f4f4f5",
  selectionInactiveBackground: "#27404a",
  black: "#1c1d20", red: "#ff7b72", green: "#8bd97f", yellow: "#e8c872",
  blue: "#6cb6ff", magenta: "#d09bf5", cyan: "#5fd4d8", white: "#d6d7da",
  brightBlack: "#6a6b72", brightRed: "#ffa198", brightGreen: "#aef0a2",
  brightYellow: "#f4dc98", brightBlue: "#9ccfff", brightMagenta: "#e2bdfa",
  brightCyan: "#8de8ea", brightWhite: "#f7f7f8",
};

/* Teclas que a janela trata e o terminal NAO pode receber. Sem isto o xterm manda o
   caractere pro shell e a janela executa o atalho, os dois ao mesmo tempo. */
// A busca e Ctrl+Shift+F, como no Windows Terminal: com Ctrl+F ela tomava a tecla do programa da
// aba, e no less, no vim e no Git Bash o Ctrl+F nunca chegava
// O P da paleta e o D da divisao seguem a mesma regra: com Shift o xterm mandaria Ctrl+P, que volta
// o historico do shell, e Ctrl+D, que fecha o shell no Git Bash e no WSL
const ATALHOS_DA_JANELA = new Set(["KeyT", "KeyW", "KeyG", "KeyF", "KeyU", "KeyP", "KeyD"]);
const ATALHOS_DE_CONTROLE = new Set([
  "Comma", "Tab", "PageUp", "PageDown",
  "Equal", "Minus", "Digit0", "NumpadAdd", "NumpadSubtract", "Numpad0",
]);

function pilhaDeFonte(nome) {
  const escolhida = nome || "Hack";
  return '"' + escolhida + '", Hack, "JetBrains Mono", Consolas, monospace';
}

function escapar(texto) {
  /* O apelido da conta e digitado por voce e vai pro innerHTML: com < ou & ele quebrava o
     botao, e com uma tag inteira virava HTML dentro de uma pagina que fala com os shells.
     Mora aqui desde que a tela de Uso antiga saiu; o painel de uso usa a mesma. */
  return String(texto).replace(/[&<>"']/g, (c) => "&#" + c.charCodeAt(0) + ";");
}

function maiusculaInicial(parte) {
  return parte ? parte.charAt(0).toUpperCase() + parte.slice(1) : parte;
}

function nomeDaFerramenta(s, dona) {
  /* Quem gerou a conversa. A sessao do Codex sem conta cadastrada caia na conta de sempre, que e
     do Claude, e o cartao dizia Claude Code numa conversa do Codex. */
  if (s && s.cli === "codex") return dona && dona.ferramenta === "codex" ? dona.ferramentaNome : "Codex";
  return dona ? dona.ferramentaNome : "Claude Code";
}

function nomeDeModelo(bruto) {
  /* O id do transcript vira nome de gente na barra: saia sonnet-4-5, ou Sonnet 4 5 com espaco
     no lugar do ponto. O que ja vem bonito da status line passa direto. */
  if (!bruto) return "";
  const texto = String(bruto);
  // o Codex grava gpt-6.1-sol e codex-auto-review, que saiam crus na barra e no painel
  const gpt = /^gpt-(\d+(?:\.\d+)?)((?:-[a-z0-9]+)*)$/i.exec(texto);
  if (gpt) return "GPT-" + gpt[1] + gpt[2].split("-").filter(Boolean).map(maiusculaInicial).map((p) => " " + p).join("");
  if (/^codex-[a-z-]+$/i.test(texto)) return texto.split("-").map(maiusculaInicial).join(" ");
  if (!texto.startsWith("claude-")) return texto;
  return texto
    .replace(/^claude-/, "")
    .replace(/-\d{8}$/, "")
    .replace(/-latest$/, "")
    .replace(/(\d+)-(\d+)/g, "$1.$2")
    .split("-")
    .map((parte) => (/^\d/.test(parte) ? parte : parte.charAt(0).toUpperCase() + parte.slice(1)))
    .join(" ");
}

/* Caminho de arquivo com numero de linha, que e o formato que o Claude Code imprime o dia
   inteiro. Aceita caminho do Windows e do Unix, com ou sem coluna no fim. */
const ARQUIVO_COM_LINHA =
  /(?:[A-Za-z]:[\\/]|\\\\|\/)[^:"<>\r\n]*?\.[A-Za-z0-9]{1,12}:\d+(?::\d+)?|(?:[^\s:"'<>|()[\]{}\\/]+[\\/])*[^\s:"'<>|()[\]{}\\/]+\.[A-Za-z0-9]{1,12}:\d+(?::\d+)?/g;

function abrirExterno(alvo, aba) {
  /* URL vai pro navegador; caminho com linha vai pro editor. O caminho impresso costuma ser
     relativo, entao vai junto a pasta daquela aba pra ele ser resolvido: a da conversa do
     Claude, a que o shell anunciou por OSC, a em que a aba nasceu e por fim a pessoal. Sem
     isso, o tests/x.py:42 do pytest numa aba de PowerShell voltava sempre Nao achei. */
  if (!bridge || !alvo) return;
  const base =
    (aba && aba.sessao && aba.sessao.pasta) ||
    (aba && aba.pastaAtual) ||
    (aba && aba.pedidoInicial && aba.pedidoInicial.pasta) ||
    pastaPessoal ||
    "";
  bridge.abrirExterno(alvo, base, (erro) => {
    if (erro) piscarAviso(erro);
  });
}

let dicaDoCtrlDada = false;

function abrirLink(ev, alvo, aba) {
  /* So com Ctrl, como no VS Code e no Windows Terminal. Com o clique simples, clicar num painel
     da grade pra dar foco, caindo em cima de um arquivo:linha, abria o editor junto. */
  if (!ev || !ev.ctrlKey) {
    if (!dicaDoCtrlDada) {
      dicaDoCtrlDada = true;
      piscarAviso("Ctrl+clique abre o link");
    }
    return;
  }
  abrirExterno(alvo, aba);
}

function pastaDoOsc(dado) {
  /* O OSC 7 vem como file://maquina/C:/pasta, com a pasta codificada como URL; o 9;9 do
     ConEmu, que o oh-my-posh tambem manda, traz o caminho do Windows entre aspas. */
  let texto = String(dado || "").trim();
  if (texto.startsWith("file://")) {
    texto = texto.slice(7);
    const barra = texto.indexOf("/");
    texto = barra >= 0 ? texto.slice(barra) : "";
    try {
      texto = decodeURIComponent(texto);
    } catch (e) {
      return "";
    }
    if (/^\/[A-Za-z]:/.test(texto)) texto = texto.slice(1);
  }
  return texto.replace(/^"|"$/g, "");
}

function colunaDoTexto(linha, deslocamento, fim = false) {
  // O texto usa offsets UTF-16, mas links do xterm usam colunas: um emoji
  // pode ocupar duas, e uma letra com acento combinado ocupa apenas uma.
  if (typeof linha.getCell !== "function" || typeof linha.length !== "number")
    return deslocamento + (fim ? 0 : 1);
  let indice = 0;
  for (let coluna = 0; coluna < linha.length; coluna++) {
    const celula = linha.getCell(coluna);
    if (!celula || celula.getWidth() === 0) continue;
    const proximo = indice + (celula.getChars() || " ").length;
    if (fim && deslocamento <= proximo) return coluna + celula.getWidth();
    if (!fim && deslocamento < proximo) return coluna + 1;
    indice = proximo;
  }
  return linha.length + (fim ? 0 : 1);
}

function provedorDeArquivos(aba) {
  return {
    provideLinks(numero, entregar) {
      const linha = aba.term.buffer.active.getLine(numero - 1);
      if (!linha) return entregar(undefined);
      const texto = linha.translateToString(true);
      const achados = [];
      ARQUIVO_COM_LINHA.lastIndex = 0;
      let m;
      while ((m = ARQUIVO_COM_LINHA.exec(texto)) !== null) {
        // url tem porta e viraria falso positivo aqui; dela cuida o addon de links
        if (texto.slice(Math.max(0, m.index - 8), m.index).includes("//")) continue;
        // Nunca transforma a cauda de um caminho absoluto ou de URL em caminho relativo.
        if (m.index && /[^\s"'([{:]/.test(texto[m.index - 1])) continue;
        if (m.index > 1 && texto[m.index - 1] === "'" && /[^\s([{]/.test(texto[m.index - 2])) continue;
        const inicio = colunaDoTexto(linha, m.index);
        const fim = colunaDoTexto(linha, m.index + m[0].length, true);
        achados.push({
          range: { start: { x: inicio, y: numero }, end: { x: fim, y: numero } },
          text: m[0],
          activate: (ev, alvo) => abrirLink(ev, alvo, aba),
        });
      }
      entregar(achados.length ? achados : undefined);
    },
  };
}

const el = (id) => document.getElementById(id);
const elAbas = el("abas");
const elPalco = el("palco");
const elMenu = el("menu");

let bridge = null;
let shells = [];
let perfis = [];
let ferramentas = [];
let elevacao = { disponivel: false, motivo: "sem sudo" };
let cfg = {};
// o motor de desenho desta subida, escolhido pelo app antes de a pagina existir
let desenho = { emUso: "gpu" };
const porSessao = new Map();
// saida que chegou antes de a aba saber o proprio id, guardada por sessao
const pendentes = new Map();
const ordem = [];
let abaAtiva = null;
let removendo = null;
// a tela dividida em dois, { esq, dir }, ou null; a aba em foco e sempre um dos lados
let divisao = null;
// a aba em que voce estava antes da atual: o atalho de dividir poe ela do outro lado
let abaAnterior = null;

/* ------------------------------------------------------------- aba base */

class AbaBase {
  constructor(rotulo) {
    this.ehPainel = true;
    this.sessionId = null;
    this.viva = true;

    this.tela = document.createElement("div");
    this.tela.className = "tela";
    elPalco.appendChild(this.tela);

    this.botao = document.createElement("div");
    this.botao.className = "sessao aba";
    this.botao.innerHTML =
      '<div class="sessao-topo">' +
      '<span class="sessao-pulso ponto"></span>' +
      '<span class="sessao-pasta rotulo"></span>' +
      '<span class="sessao-idade"></span>' +
      "</div>" +
      '<div class="sessao-origem"></div>' +
      '<div class="sessao-linha"></div>' +
      '<div class="sessao-rodape"></div>';
    this.botao.querySelector(".rotulo").textContent = rotulo;
    this.rotuloBase = rotulo;
    this.botao.addEventListener("mousedown", (ev) => {
      if (ev.button === 0) ativar(this);
    });
    this.botao.addEventListener("contextmenu", (ev) => {
      ev.preventDefault();
      abrirMenuAba(this, ev.clientX, ev.clientY);
    });
    elAbas.appendChild(this.botao);
    ordem.push(this);
  }

  foco() {}
  ajustar() {}
  aplicar() {}

  destruir() {
    const i = ordem.indexOf(this);
    if (i >= 0) ordem.splice(i, 1);
    this.tela.remove();
    this.botao.remove();
  }
}

/* --------------------------------------------------------- aba terminal */

function montarRolagemTerminal(term, tela, aoAtivar = () => {}) {
  // O trilho pertence ao Loom: continua visivel quando o Chromium oculta a barra nativa.
  // A rolagem e o historico continuam no xterm, pela API publica, inclusive roda/busca.
  const trilho = document.createElement("div");
  trilho.className = "rolagem-terminal";
  trilho.tabIndex = 0;
  trilho.setAttribute("role", "scrollbar");
  trilho.setAttribute("aria-label", "Histórico do terminal");
  trilho.setAttribute("aria-orientation", "vertical");
  trilho.setAttribute("aria-valuemin", "0");
  if (!term.element.id) {
    montarRolagemTerminal.proximoId = (montarRolagemTerminal.proximoId || 0) + 1;
    term.element.id = "terminal-rolagem-" + montarRolagemTerminal.proximoId;
  }
  trilho.setAttribute("aria-controls", term.element.id);
  const polegar = document.createElement("div");
  polegar.className = "rolagem-terminal-polegar";
  trilho.appendChild(polegar);
  tela.classList.add("com-rolagem");
  tela.appendChild(trilho);

  let quadro = 0, destruida = false, arrasto = null;
  const limite = () => term.buffer.active.baseY;
  const rolar = (linha) => term.scrollToLine(Math.max(0, Math.min(limite(), Math.round(linha))));
  function pintar() {
    quadro = 0;
    if (destruida) return;
    const maximo = limite();
    if (!maximo && arrasto) soltar();
    const posicao = term.buffer.active.viewportY;
    const altura = trilho.clientHeight;
    const tamanho = Math.min(altura, Math.max(32, altura * term.rows / (term.rows + maximo)));
    polegar.style.height = tamanho + "px";
    polegar.style.top = (maximo ? (altura - tamanho) * posicao / maximo : 0) + "px";
    trilho.classList.toggle("sem-historico", maximo === 0);
    trilho.setAttribute("aria-disabled", String(maximo === 0));
    trilho.setAttribute("aria-valuemax", String(maximo));
    trilho.setAttribute("aria-valuenow", String(posicao));
    trilho.title = maximo
      ? "Arraste para navegar no histórico · Home/End vão ao início/fim"
      : term.buffer.active.type === "alternate"
        ? "Este aplicativo usa tela cheia e não mantém histórico neste buffer"
        : "O histórico aparece quando a saída ultrapassa a altura do terminal";
    trilho.setAttribute("aria-valuetext", maximo ? "Linha " + posicao + " de " + maximo : trilho.title);
  }
  function agendar() {
    if (!destruida && !quadro) quadro = requestAnimationFrame(pintar);
  }
  trilho.addEventListener("pointerdown", (ev) => {
    if (ev.button !== 0) return;
    ev.preventDefault();
    aoAtivar();
    trilho.focus({ preventScroll: true });
    // Na grade, ativar tambem agenda o foco do terminal; o trilho vem depois dele.
    requestAnimationFrame(() => { if (!destruida) trilho.focus({ preventScroll: true }); });
    if (!limite()) return;
    if (ev.target !== polegar) {
      const acima = ev.clientY < polegar.getBoundingClientRect().top;
      rolar(term.buffer.active.viewportY + (acima ? -term.rows : term.rows));
      return;
    }
    arrasto = { id: ev.pointerId, y: ev.clientY, linha: term.buffer.active.viewportY };
    trilho.setPointerCapture(ev.pointerId);
    trilho.classList.add("arrastando");
  });
  trilho.addEventListener("pointermove", (ev) => {
    if (!arrasto || arrasto.id !== ev.pointerId) return;
    const percurso = trilho.clientHeight - polegar.offsetHeight;
    if (percurso > 0) rolar(arrasto.linha + (ev.clientY - arrasto.y) * limite() / percurso);
  });
  const soltar = () => {
    const id = arrasto && arrasto.id;
    arrasto = null;
    trilho.classList.remove("arrastando");
    if (id !== null && trilho.hasPointerCapture(id)) trilho.releasePointerCapture(id);
  };
  trilho.addEventListener("pointerup", soltar);
  trilho.addEventListener("pointercancel", soltar);
  trilho.addEventListener("lostpointercapture", soltar);
  trilho.addEventListener("keydown", (ev) => {
    const passos = { ArrowUp: -1, ArrowDown: 1, PageUp: -term.rows, PageDown: term.rows };
    if (!(ev.key in passos) && ev.key !== "Home" && ev.key !== "End") return;
    ev.preventDefault();
    ev.stopPropagation();
    rolar(ev.key === "Home" ? 0 : ev.key === "End" ? limite() : term.buffer.active.viewportY + passos[ev.key]);
  });
  const assinaturas = [term.onScroll(agendar), term.onResize(agendar), term.onWriteParsed(agendar),
    term.buffer.onBufferChange(() => { soltar(); agendar(); })];
  const observador = typeof ResizeObserver === "function" ? new ResizeObserver(agendar) : null;
  if (observador) observador.observe(trilho);
  agendar();
  return {
    atualizar: agendar,
    dispose() {
      destruida = true;
      soltar();
      if (quadro) window.cancelAnimationFrame(quadro);
      if (observador) observador.disconnect();
      for (const assinatura of assinaturas) assinatura.dispose();
      trilho.remove();
      tela.classList.remove("com-rolagem");
    },
  };
}

function opcoesTerminal() {
  return {
    theme: TEMA,
    fontFamily: pilhaDeFonte(cfg.fonte),
    fontSize: fonteDoTerminal(),
    lineHeight: cfg.entrelinha,
    letterSpacing: cfg.espacoLetra,
    cursorStyle: cfg.cursor,
    cursorBlink: cfg.cursorPisca,
    cursorWidth: 2,
    scrollback: cfg.historico,
    smoothScrollDuration: 0,
    allowProposedApi: true,
    windowsPty: { backend: "conpty" },
    rightClickSelectsWord: true,
  };
}

class Aba extends AbaBase {
  constructor(shell, perfil, elevado) {
    super(shell.nome);
    this.ehPainel = false;
    this.shell = shell;
    this.perfil = perfil || null;
    this.elevado = !!elevado;


    this.term = new Terminal(opcoesTerminal());
    this.fit = new FitAddon.FitAddon();
    this.term.loadAddon(this.fit);
    // Enquanto o historico guardado e redesenhado, o terminal fica no tamanho em que ele foi
    // escrito. O ativar, a grade e a janela remedem sozinhos, e um fit no meio da escrita fazia o
    // desenho do CLI antigo cair torto na medida nova.
    this.restaurando = false;
    const medir = this.fit.fit.bind(this.fit);
    this.fit.fit = () => {
      if (!this.restaurando) medir();
    };
    this.busca = new SearchAddon.SearchAddon();
    this.term.loadAddon(this.busca);
    // quantas ocorrencias e em qual voce esta; sem isso a busca so dizia "nada"
    this.busca.onDidChangeResults((r) => {
      if (abaAtiva !== this || elBusca.hidden) return;
      el("busca-conta").textContent =
        r && r.resultCount ? r.resultIndex + 1 + " de " + r.resultCount : "nada";
    });
    // sem handler proprio o addon chama window.open, que dentro do QWebEngineView devolve
    // nulo e o clique so virava uma linha no console
    this.term.loadAddon(
      new WebLinksAddon.WebLinksAddon((ev, uri) => abrirLink(ev, uri, this))
    );
    this.term.registerLinkProvider(provedorDeArquivos(this));
    // a pasta atual do shell, pros links relativos de uma aba sem Claude. Devolver false deixa
    // o OSC seguir pra quem mais quiser ler, inclusive o 9 que nao e de pasta. Ela vai junto
    // pras abas guardadas: reabrindo o app, a aba voltava pra pasta de onde nasceu
    this.term.parser.registerOscHandler(7, (dado) => {
      this.anunciouPasta(pastaDoOsc(dado));
      return false;
    });
    this.term.parser.registerOscHandler(9, (dado) => {
      if (String(dado).startsWith("9;")) this.anunciouPasta(pastaDoOsc(String(dado).slice(2)));
      return false;
    });
    this.chapeu = document.createElement("div");
    this.chapeu.className = "chapeu";
    this.chapeu.innerHTML =
      '<span class="chapeu-ponto"></span>' +
      '<span class="chapeu-conta"></span>' +
      '<span class="chapeu-nome"></span>' +
      '<span class="chapeu-estado"></span>' +
      '<span class="chapeu-medidas"></span>';
    this.tela.appendChild(this.chapeu);
    // o que roda nesta aba gasta: no cartao da lateral sempre, e no chapeu na grade e na tela dividida
    this.usoNoCartao = montarUsoDaAba("sessao-uso");
    this.botao.appendChild(this.usoNoCartao);
    // dentro do terminal, a barra de cima medindo so esta aba: no chapeu na grade, numa faixa no resto
    this.usoNoChapeu = montarMonitorDaAba("chapeu-uso");
    this.chapeu.insertBefore(this.usoNoChapeu, this.chapeu.querySelector(".chapeu-medidas"));
    this.usoNaTela = montarMonitorDaAba("tela-uso");
    this.tela.appendChild(this.usoNaTela);

    this.term.open(this.tela);
    this.tela.addEventListener("paste", (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      if (!this.viva || !this.sessionId || veuAberto()) return;
      const texto = ev.clipboardData && ev.clipboardData.getData("text/plain");
      if (texto) entregarColagem(this, texto);
      else colarAqui(this);
    }, true);
    // na grade e na tela dividida o clique num painel da o foco a ele
    this.rolagem = montarRolagemTerminal(this.term, this.tela, () => {
      if ((emGrade || naDivisao(this)) && abaAtiva !== this) ativar(this);
    });
    this.tela.addEventListener("mousedown", () => {
      if ((emGrade || naDivisao(this)) && abaAtiva !== this) ativar(this);
    });
    /* O botao direito abre o nosso menu, sempre. Deixando passar, quem respondia era o
       menu do Chromium, com Recarregar dentro: recarregar a pagina largaria todos os
       ConPTY vivos sem tela. O xterm ja selecionou a palavra no mousedown, entao
       Copiar a selecao chega com o que esta debaixo do cursor. */
    this.tela.addEventListener(
      "contextmenu",
      (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        if (abaAtiva !== this) ativar(this);
        abrirMenuAba(this, ev.clientX, ev.clientY);
      },
      true
    );
    // o WebGL nao nasce aqui: so quando a aba aparece, pelo sincronizarWebgl
    this.webgl = null;
    this.telasWebgl = [];

    this.pintar();

    this.term.onData((d) => {
      if (!this.viva) {
        // shell encerrado: Enter sobe outro no lugar em vez de a aba virar lapide
        if (d === "\r" || d === "\n") this.reabrir();
        return;
      }
      if (bridge && this.sessionId) bridge.write(this.sessionId, d);
      // apertou Enter: mandou algo, entao a lista tem que reagir junto
      if (d.charCodeAt(0) === 13 || d.charCodeAt(0) === 10) setTimeout(atualizarSessoes, 250);
    });
    // o pwsh, o vim e o npm anunciam o que estao fazendo por OSC; aba de shell puro
    // deixa de se chamar PowerShell 7 pra sempre
    this.term.onTitleChange((t) => {
      const limpo = (t || "").trim().slice(0, 60);
      if (limpo === this.tituloOsc) return;
      this.tituloOsc = limpo;
      if (this.sessao) return;
      this.pintar();
      // na grade o topo do painel e o que diz qual e qual, e ficava com o nome velho
      if (emGrade || naDivisao(this)) pintarChapeu(this);
    });
    this.term.onResize((dim) => {
      if (bridge && this.sessionId) bridge.resize(this.sessionId, dim.cols, dim.rows);
    });
    this.term.attachCustomKeyEventHandler((ev) => {
      // Com uma caixa aberta nenhuma tecla vai pro shell. O xterm segura o Esc e o Enter e a
      // janela nem ficava sabendo: o Esc pra cancelar interrompia o Claude e a caixa ficava.
      // Devolvendo false a tecla sobe ate a janela, que fecha a caixa.
      if (veuAberto()) return false;
      // O menu do botao direito abre com o foco ainda aqui. O Esc pra desistir dele caia no
      // shell e interrompia o Claude, e o menu ficava; agora ele so fecha o menu. Outra tecla
      // fecha o menu e segue pro terminal, que e onde a pessoa esta digitando.
      if (menuAberto()) {
        if (ev.key === "Escape") return false;
        if (ev.type === "keydown" && !TECLAS_SOLTAS.has(ev.key)) fecharMenus();
      }
      if (ev.type !== "keydown") return true;
      // Esc com texto selecionado so solta a selecao, que e o que o aviso promete. Chegando
      // no shell ele interrompia o turno do Claude, e no PowerShell apagava a linha digitada
      if (ev.key === "Escape" && !ev.ctrlKey && !ev.altKey && !ev.shiftKey && this.term.hasSelection()) {
        ev.preventDefault();
        ev.stopPropagation();
        this.term.clearSelection();
        return false;
      }
      // Shift+Enter quebra linha em vez de enviar. E o que o /terminal-setup do Claude Code
      // instala nos outros terminais, e ele nao conhece o Loom, entao fazemos aqui.
      if (ev.key === "Enter" && ev.shiftKey && !ev.ctrlKey && !ev.altKey) {
        ev.preventDefault();
        if (bridge && this.sessionId) bridge.write(this.sessionId, "\x1b\r");
        return false;
      }
      // Ctrl+Shift+menos e o Ctrl+_ que desfaz no Claude Code; so sem Shift e zoom
      if (ev.ctrlKey && ev.shiftKey && ev.code === "Minus") return true;
      // tudo que a janela trata precisa sair daqui, senao o caractere ainda vai pro shell
      if (ev.ctrlKey && ev.shiftKey && ATALHOS_DA_JANELA.has(ev.code)) return false;
      if (ev.ctrlKey && ATALHOS_DE_CONTROLE.has(ev.code)) return false;
      if (ev.altKey && !ev.ctrlKey && /^Digit[1-9]$/.test(ev.code)) return false;
      if (ev.code === "F3") return false;

      if (ev.ctrlKey && !ev.altKey && ev.code === "KeyV") {
        ev.preventDefault();
        colarAqui(this);
        return false;
      }
      if (ev.ctrlKey && ev.code === "KeyC" && this.term.hasSelection()) {
        ev.preventDefault();
        navigator.clipboard.writeText(this.term.getSelection());
        this.term.clearSelection();
        return false;
      }
      return true;
    });

    // botao do meio fecha e duplo clique no nome renomeia, como em toda barra de abas
    this.botao.addEventListener("mousedown", (ev) => {
      // sem isto o Chromium liga a rolagem automatica no botao do meio
      if (ev.button === 1) ev.preventDefault();
    });
    this.botao.addEventListener("auxclick", (ev) => {
      if (ev.button !== 1) return;
      ev.preventDefault();
      fecharComCuidado(this);
    });
    this.botao.querySelector(".rotulo").addEventListener("dblclick", (ev) => {
      ev.stopPropagation();
      renomearAba(this);
    });
    const fecharX = document.createElement("button");
    fecharX.className = "sessao-fechar";
    fecharX.title = "Fechar a aba, Ctrl+Shift+W";
    fecharX.textContent = "×";
    // no mousedown o cartao ativaria a aba que esta sendo fechada
    fecharX.addEventListener("mousedown", (ev) => ev.stopPropagation());
    fecharX.addEventListener("click", (ev) => {
      ev.stopPropagation();
      fecharComCuidado(this);
    });
    this.botao.querySelector(".sessao-topo").appendChild(fecharX);

    // Ctrl com a roda muda o tamanho da fonte, como em todo terminal e navegador
    this.tela.addEventListener(
      "wheel",
      (ev) => {
        if (!ev.ctrlKey) return;
        ev.preventDefault();
        zoom(ev.deltaY < 0 ? 1 : -1);
      },
      { passive: false }
    );
  }

  nomeDoCartao() {
    /* Uma fonte so pro nome da aba, em ordem de quem sabe mais: o apelido que voce deu,
       o nome vivo que a status line manda, o nome da conversa no transcript, o titulo que
       o proprio shell anuncia por OSC, e por fim o nome do shell. */
    if (this.apelido) return this.apelido;
    const vivo = tituloVivoDaAba(this);
    if (vivo) return vivo;
    if (this.sessao) {
      const dona = this.perfil || contaPadraoCadastrada();
      return tituloDaConversa(this.sessao, nomeDaFerramenta(this.sessao, dona));
    }
    if (this.tituloOsc) return this.tituloOsc;
    return this.shell.nome + (this.elevado ? " (adm)" : "");
  }

  anunciouPasta(pasta) {
    if (!pasta || pasta === this.pastaAtual) return;
    this.pastaAtual = pasta;
    anotarAbas();
  }

  pintar() {
    const dona = this.perfil || contaPadraoCadastrada();
    const ponto = this.botao.querySelector(".ponto");
    const cor = dona ? dona.cor : "#5b6371";
    this.botao.style.borderLeftColor = this.viva ? cor : "#2a2f38";
    // O ponto e do estado: a cor da conta ja esta na borda e no rodape. Em linha ela vencia as
    // regras de estado e o ready so se distinguia por uma palavra. Sem Claude, fica a da conta.
    ponto.style.background = "";
    ponto.style.setProperty("--cor-conta", this.viva ? cor : "#3a3f49");

    const rodape = this.botao.querySelector(".sessao-rodape");
    rodape.innerHTML = "";
    const conta = document.createElement("span");
    conta.className = "sessao-conta";
    conta.textContent = dona ? dona.nome : "Principal";
    if (dona) conta.style.color = dona.cor;
    rodape.appendChild(conta);
    const nome = this.nomeDoCartao();
    // o nome do shell so vai no rodape quando o titulo diz outra coisa; sem isso saia repetido
    if (!this.sessao && nome !== this.shell.nome + (this.elevado ? " (adm)" : "")) {
      const shell = document.createElement("span");
      shell.className = "sessao-shell";
      shell.textContent = this.shell.nome;
      rodape.appendChild(shell);
    }

    if (!this.sessao) {
      this.botao.classList.remove("pensando", "rodando", "aguardando", "parada");
      const titulo = this.botao.querySelector(".rotulo");
      titulo.textContent = nome;
      titulo.title = this.tituloOsc && !this.apelido ? this.shell.nome : "";
      const origem = this.botao.querySelector(".sessao-origem");
      if (origem) {
        // a aba reaberta diz qual conversa rodava nela, e onde ela volta
        const antes = this.viva && this.conversaAnterior ? this.tituloAnterior || "a conversa de antes" : "";
        origem.textContent = antes ? "antes: " + antes : "";
        origem.title = antes ? "Botão direito, Retomar a conversa de antes" : "";
      }
      const linha = this.botao.querySelector(".sessao-linha");
      linha.textContent = this.viva ? "shell" : "closed";
      // o Claude da conta de sempre so aparece no cartao com ela ligada, e o cartao dizia shell calado
      linha.title =
        this.viva && principalSemLeitura(dona)
          ? "O Claude da Principal só aparece aqui depois de ligar em Configurações, Contas"
          : "";
      this.botao.querySelector(".sessao-idade").textContent = "";
    }
  }

  refletir(s) {
    /* Traz pro cartao da aba o estado da sessao de Claude que roda dentro dela. */
    this.sessao = s || null;
    const donaAgora = this.perfil || contaPadraoCadastrada();
    // Repintar o cartao inteiro a cada ciclo era uma dezena de mutacoes de DOM por aba
    // sem nada mudar na tela. Com a chave, so a idade anda quando o resto e igual.
    if (this.renomeando) return;
    const chave = s
      ? [
          s.estado, s.espera, s.titulo, s.ferramenta, s.alvo, s.pasta, s.contexto, s.modelo,
          this.viva, donaAgora && donaAgora.id, donaAgora && donaAgora.cor,
          tituloVivoDaAba(this), this.apelido, this.tituloOsc,
        ].join("|")
      : "";
    if (chave && chave === this.chaveRefletida) {
      const idade = this.botao.querySelector(".sessao-idade");
      const texto = tempoCurto(s.idade);
      if (idade && idade.textContent !== texto) idade.textContent = texto;
      return;
    }
    this.chaveRefletida = chave;
    const cartao = this.botao;
    const estados = ["pensando", "rodando", "aguardando", "parada"];
    cartao.classList.remove(...estados);
    this.tela.classList.remove(...estados);
    if (!s) {
      this.pintar();
      return;
    }
    cartao.classList.add(s.estado);
    this.tela.classList.add(s.estado);

    const dona = this.perfil || contaPadraoCadastrada();
    const ferramenta = nomeDaFerramenta(s, dona);
    const nome = this.nomeDoCartao();
    const titulo = cartao.querySelector(".rotulo");
    titulo.textContent = nome;
    titulo.title = nome;
    pintarOrigem(cartao, ferramenta, s, nome);
    cartao.querySelector(".sessao-idade").textContent = tempoCurto(s.idade);

    pintarLinhaDeEstado(cartao.querySelector(".sessao-linha"), s);

    this.pintar();
    if (typeof s.contexto === "number") {
      cartao.querySelector(".sessao-rodape").appendChild(barraContexto(s.contexto));
    }
  }

  abrir(pedido) {
    /* A aba reaberta com historico guardado redesenha o que mostrava antes de o shell subir, e
       so depois mede e abre, pra saida nova nao se misturar com a antiga. O texto fica so aqui:
       pro app vai o id antigo, e ele junta o arquivo guardado a memoria do shell novo. */
    const restauro = pedido && pedido.restauro;
    if (pedido) {
      pedido = Object.assign({}, pedido);
      delete pedido.restauro;
    }
    // o Enter na aba encerrada reabre a partir daqui, e o id antigo era so desta abertura
    this.pedidoInicial = pedido ? Object.assign({}, pedido) : null;
    if (this.pedidoInicial) delete this.pedidoInicial.anterior;
    this.sessaoAnterior = (pedido && pedido.anterior) || "";
    ativar(this);
    const subir = (emenda) => {
      // a tela acabou de nascer: medir agora daria tamanho errado e o CLI desenharia torto
      setTimeout(() => {
        if (this.destruida) return;
        this.fit.fit();
        const perfilId = this.perfil ? this.perfil.id : "";
        bridge.openSessionWith(
          this.shell.id,
          perfilId,
          this.term.cols,
          this.term.rows,
          JSON.stringify(Object.assign({}, pedido, { elevar: this.elevado }, emenda ? { emenda } : {})),
          (id) => {
            if (this.destruida) {
              // a aba fechou antes de o id voltar: o shell ja tinha subido e ficava vivo sem
              // tela ate o app fechar, com o CLI da conta digitado dentro dele logo depois
              bridge.closeSession(id);
              pendentes.delete(id);
              return;
            }
            if (!id) {
              // o app nao abriu o shell e nem deu id: a aba ficava viva sem shell, engolindo cada
              // tecla. Encerrada, o Enter tenta de novo
              this.encerrada(-1);
              return;
            }
            this.sessionId = id;
            porSessao.set(id, this);
            for (const data of pendentes.get(id) || []) this.term.write(data);
            pendentes.delete(id);
            if (abaAtiva === this) this.term.focus();
            // o CLI leva um instante pra desenhar; remedir aqui evita quebra de layout
            setTimeout(() => this.ajustar(), 400);
          }
        );
      }, 0);
    };
    if (!restauro || !restauro.texto) {
      subir("");
      return;
    }
    this.restaurando = true;
    if (restauro.cols > 0 && restauro.rows > 0) this.term.resize(restauro.cols, restauro.rows);
    this.term.write(restauro.texto);
    // a posicao da linha de Restaurado so se mede depois de o estado do programa antigo zerado:
    // sair da tela alternativa troca o que aparece
    this.term.write(ZERAR_DEPOIS_DO_RESTAURO, () => {
      if (this.destruida) return;
      const emenda = emendaDoRestauro(linhasDaTela(this.term), restauro.em);
      this.term.write(emenda, () => {
        this.restaurando = false;
        subir(ZERAR_DEPOIS_DO_RESTAURO + emenda);
      });
    });
  }

  ligar(viva) {
    /* Reencontra um shell que continuou rodando enquanto a tela era refeita, em vez de subir
       outro. O terminal e redesenhado com o que o shell ja tinha mostrado. */
    this.pedidoInicial = { pasta: viva.pasta || "" };
    this.sessionId = viva.id;
    porSessao.set(viva.id, this);
    // o historico ja traz tudo que estava na fila; repetir a fila escreveria duas vezes
    pendentes.delete(viva.id);
    // redesenha no tamanho em que o shell escreveu. Medir aqui nao serve: aba escondida nao
    // tem tamanho, e mandar isso pro shell espremeria o CLI. Quem acerta a medida e o ativar.
    if (viva.cols > 0 && viva.rows > 0) this.term.resize(viva.cols, viva.rows);
    if (viva.historico) this.term.write(viva.historico);
  }

  escrever(data) {
    // o Claude redesenha o contador da tela enquanto trabalha, entao saida recente e sinal
    // de trabalho, mesmo com o transcript calado durante uma ferramenta longa
    this.ultimaSaida = Date.now();
    this.term.write(data);
  }

  encerrada(code) {
    this.viva = false;
    this.chaveRefletida = "";
    this.pintar();
    this.term.write(
      "\r\n\x1b[38;5;240m[sessão encerrada, código " + code + " · Enter abre outra]\x1b[0m\r\n"
    );
  }

  reabrir() {
    /* Sobe um shell novo na mesma aba, com a mesma conta e a mesma pasta de antes. */
    if (this.viva) return;
    if (this.sessionId) porSessao.delete(this.sessionId);
    this.sessionId = null;
    this.viva = true;
    this.chaveRefletida = "";
    this.term.reset();
    // a ultima pasta que a aba conheceu: a da conversa, a que o shell anunciou por OSC ou a
    // de onde nasceu. So a de nascimento fazia o Enter voltar pra pasta pessoal depois de um cd.
    // O /login nao se repete: ele era pedido daquela primeira abertura, nao desta. Nem o CLI
    // segurado da aba reaberta com conversa: o shell novo abre como uma aba nova abriria.
    const pedido = Object.assign({}, this.pedidoInicial);
    const pasta = pastaDaAba(this);
    if (pasta) pedido.pasta = pasta;
    delete pedido.entrar;
    delete pedido.semCli;
    this.abrir(pedido);
  }

  foco() {
    this.term.focus();
  }

  ajustar() {
    const antes = this.term.cols + "x" + this.term.rows;
    this.fit.fit();
    if (this.sessionId && antes !== this.term.cols + "x" + this.term.rows) {
      bridge.resize(this.sessionId, this.term.cols, this.term.rows);
    }
  }

  aplicar() {
    // trocar uma opcao do xterm remede a fonte e limpa o atlas de glifos, entao so
    // vale mexer no que de fato mudou
    const o = opcoesTerminal();
    let mudou = false;
    for (const chave of ["fontFamily", "fontSize", "lineHeight", "letterSpacing", "cursorStyle", "cursorBlink", "scrollback"]) {
      if (this.term.options[chave] === o[chave]) continue;
      this.term.options[chave] = o[chave];
      mudou = true;
    }
    if (mudou) {
      this.fit.fit();
      this.rolagem.atualizar();
    }
  }

  ligarWebgl() {
    /* O contexto WebGL so existe enquanto a aba aparece. A escondida fica em visibility hidden,
       e o contexto dela seguia vivo fora da vista, cada um mais um caminho pro ANGLE D3D11, que
       e onde a tela cai levando o Loom inteiro. Escondida, o xterm desenha pelo DOM. */
    if (this.webgl || !webglPermitido()) return;
    try {
      const antes = new Set(this.tela.querySelectorAll("canvas"));
      const webgl = new WebglAddon.WebglAddon();
      // O addon so avisa depois de 3 s sem o contexto voltar. Sem tratar, o terminal congelava
      // no ultimo quadro; largando o addon o xterm volta pro DOM e a aba continua viva.
      webgl.onContextLoss(() => {
        this.largarWebgl();
        perdeuContexto();
      });
      this.term.loadAddon(webgl);
      this.webgl = webgl;
      this.telasWebgl = Array.from(this.tela.querySelectorAll("canvas")).filter((c) => !antes.has(c));
    } catch (e) {
      this.webgl = null;
      console.warn("sem webgl, caiu no DOM", e);
    }
  }

  largarWebgl() {
    const webgl = this.webgl;
    if (!webgl) return;
    this.webgl = null;
    try {
      webgl.dispose();
    } catch (e) {
      console.warn("o webgl nao largou direito", e);
    }
    // O contexto largado so sai da conta do Chromium quando o coletor de lixo passa, e a conta
    // tem teto de 16 por pagina: trocando de aba ligeiro ela estourava, e o Chromium matava o mais
    // velho, que podia ser o de uma aba a vista. Perdido de proposito, sai da conta na hora. O
    // addon ja largado nao escuta mais a perda, entao ela nao conta como falha da placa.
    for (const tela of this.telasWebgl) {
      const gl = tela.getContext("webgl2");
      const perder = gl && !gl.isContextLost() && gl.getExtension("WEBGL_lose_context");
      if (perder) perder.loseContext();
    }
    this.telasWebgl = [];
  }

  destruir() {
    this.destruida = true;
    if (bridge && this.sessionId && this.viva) bridge.closeSession(this.sessionId);
    if (this.sessionId) porSessao.delete(this.sessionId);
    this.largarWebgl();
    this.rolagem.dispose();
    this.term.dispose();
    super.destruir();
  }
}

/* -------------------------------------------------------------- webgl */

// o Chromium derruba o contexto mais velho passando de 16; com folga, na grade grande o resto vai pelo DOM
const TETO_DE_WEBGL = 12;
// duas perdas de contexto dentro disto e a placa falhando, e nao uma perda solta
const JANELA_DAS_PERDAS_MS = 2000;
let perdasDeContexto = [];
let placaDesistida = false;

function webglPermitido() {
  // no desenho por software nao ha placa na pagina: nem tenta, o xterm vai direto pro DOM
  return !placaDesistida && desenho.emUso !== "software";
}

function sincronizarWebgl() {
  /* Liga o WebGL nas abas que aparecem, a ativa e, na grade, os paineis, e larga nas outras.
     Larga antes de ligar, pra conta de contextos nunca passar do teto nem por um instante. Na tela
     dividida os dois lados aparecem. */
  const aVista = ordem.filter((a) => !a.ehPainel && (a === abaAtiva || emGrade || naDivisao(a)));
  // com mais paineis que o teto, a ativa e a que nao pode ficar sem
  aVista.sort((a, b) => (b === abaAtiva) - (a === abaAtiva));
  const comWebgl = new Set(aVista.slice(0, TETO_DE_WEBGL));
  for (const aba of ordem) if (!aba.ehPainel && !comWebgl.has(aba)) aba.largarWebgl();
  for (const aba of comWebgl) aba.ligarWebgl();
}

function perdeuContexto(agora) {
  /* Uma perda solta acontece, o driver reinicia e volta. Duas em 2 s e a placa falhando, e e dali
     que vem a queda que derruba o Loom inteiro: a pagina larga o WebGL de todas as abas ate o fim
     e o app anota a queda, pro motor subir por software na proxima vez. */
  const instante = agora === undefined ? Date.now() : agora;
  perdasDeContexto = perdasDeContexto.filter((t) => instante - t < JANELA_DAS_PERDAS_MS);
  perdasDeContexto.push(instante);
  if (perdasDeContexto.length < 2 || placaDesistida) return false;
  placaDesistida = true;
  for (const aba of ordem) if (!aba.ehPainel) aba.largarWebgl();
  if (bridge) bridge.gpuPerdida();
  return true;
}

/* ------------------------------------------------------------- controle */

function ativar(aba) {
  if (abaAtiva === aba) return;
  if (emGrade && aba.ehPainel) alternarGrade(false);
  // na tela dividida a aba escolhida entra no lado em foco; a que ja esta do outro lado so ganha o foco
  if (divisao) trocarNaDivisao(aba);
  const saindo = abaAtiva;
  if (saindo) abaAnterior = saindo;
  if (abaAtiva) {
    abaAtiva.tela.classList.remove("ativa");
    abaAtiva.botao.classList.remove("ativa");
  }
  abaAtiva = aba;
  aba.tela.classList.add("ativa");
  aba.botao.classList.add("ativa");
  sincronizarWebgl();
  mostrarNaLateral(aba);
  // a busca aberta acompanha a aba: a contagem era da anterior, e o realce ficava nela
  if (!elBusca.hidden) {
    if (saindo && saindo.busca) saindo.busca.clearDecorations();
    if (aba.ehPainel) fecharBusca();
    else buscar(false, true);
  }
  pintarUsoDaBarra();
  requestAnimationFrame(() => {
    aba.ajustar();
    aba.foco();
  });
  anotarAbas();
}

function mostrarNaLateral(aba) {
  /* Ctrl+Tab, Alt com numero e o clique na notificacao ativavam uma aba com o cartao fora da
     vista, e o destaque ficava escondido. Rola so a lista, na mao: o scrollIntoView tambem
     mexeria nos pais que escondem o que passa da borda. */
  const cartao = aba.botao;
  if (!cartao || cartao.parentNode !== elAbas) return;
  const lista = elAbas.getBoundingClientRect();
  const caixa = cartao.getBoundingClientRect();
  if (caixa.top < lista.top) elAbas.scrollTop -= lista.top - caixa.top;
  else if (caixa.bottom > lista.bottom) elAbas.scrollTop += caixa.bottom - lista.bottom;
}

function fechar(aba) {
  const i = ordem.indexOf(aba);
  const eraAtiva = abaAtiva === aba;
  // fechar um dos lados desfaz a divisao, e o foco vai pro lado que ficou
  const lado = naDivisao(aba) ? outroLado(aba) : null;
  if (lado) desfazerDivisao();
  if (abaAnterior === aba) abaAnterior = null;
  aba.destruir();
  atualizarVazio();
  if (eraAtiva) {
    abaAtiva = null;
    const alvo = lado && ordem.indexOf(lado) >= 0 ? lado : ordem[Math.min(i, ordem.length - 1)];
    if (alvo) ativar(alvo);
  }
  montarSessoes();
  anotarAbas();
  pintarUsoDaBarra();
  if (emGrade) {
    // na grade acima do teto, o painel que ficou sem WebGL ganha a vaga do que fechou
    sincronizarWebgl();
    pintarGrade();
    ajustarTodas();
  }
}

function novaAba(shellId, perfilId, pedido) {
  // sem shell pedido vale o padrao das configuracoes: a aba aberta pelo menu da conta ou
  // pelo retomar caia sempre no primeiro shell da lista, ignorando a escolha feita ali
  const shell = shells.find((s) => s.id === (shellId || cfg.shellPadrao)) || shells[0];
  const perfil = perfis.find((p) => p.id === perfilId) || null;
  if (!shell) return;
  const aba = new Aba(shell, perfil, (pedido || {}).elevar);
  aba.abrir(pedido);
  montarSessoes();
  if (emGrade) {
    pintarGrade();
    ajustarTodas();
  }
  setTimeout(atualizarSessoes, 300);
  return aba;
}

function reconectarAbas(depois) {
  /* Os shells moram no processo do app, nao na pagina. Quando a tela e refeita, porque o
     motor web caiu ou parou de desenhar, cada aba volta ligada ao shell que continuou
     rodando. No start normal a lista vem vazia e segue a restauracao de sempre. */
  bridge.liveSessions((json) => {
    let vivas = [];
    try {
      vivas = JSON.parse(json);
    } catch (e) {
      vivas = [];
    }
    let ativa = null;
    for (const v of vivas) {
      const shell = shells.find((s) => s.id === v.shell) || shells[0];
      if (!shell) continue;
      const perfil = perfis.find((p) => p.id === v.perfil) || null;
      const aba = new Aba(shell, perfil, v.elevado);
      aba.conversaAnterior = v.conversa || "";
      aba.tituloAnterior = v.titulo || "";
      if (v.apelido) {
        aba.apelido = v.apelido;
        aba.chaveRefletida = "";
        aba.pintar();
      }
      aba.ligar(v);
      if (v.ativa || !ativa) ativa = aba;
    }
    if (!ativa) {
      depois(false);
      return;
    }
    ativar(ativa);
    montarSessoes();
    setTimeout(atualizarSessoes, 300);
    // a divisao volta junto, pela anotacao das abas que o app guardou em memoria
    bridge.ladosDaDivisao((json) => {
      let lados = {};
      try {
        lados = JSON.parse(json) || {};
      } catch (e) {
        lados = {};
      }
      const esq = porSessao.get(lados.esq);
      const dir = porSessao.get(lados.dir);
      if (esq && dir) dividir(esq, dir, abaAtiva === dir ? dir : esq);
    });
    depois(true);
  });
}

function abaPadrao() {
  novaAba(cfg.shellPadrao || (shells.length ? shells[0].id : ""), cfg.perfilPadrao || "");
}

function contaDoPainel() {
  /* O id que o painel de uso da a conta da aba ativa: provedor e perfil, ou "padrao" pra conta
     sem perfil. Quem clicava no 5h da conta Trabalho caia no painel mostrando outra conta. */
  const chave = chaveDaAba();
  const perfil = perfis.find((p) => p.id === chave);
  if (!perfil) return "claude:padrao";
  return (perfil.ferramenta || "claude") + ":" + perfil.id;
}

function faixaComTexto(term) {
  // selectAll marca o buffer inteiro, e num app de tela cheia como o Claude Code
  // isso e quase tudo linha vazia. Aqui achamos onde o texto realmente comeca e acaba.
  const buf = term.buffer.active;
  let inicio = -1;
  let fim = -1;
  for (let i = 0; i < buf.length; i += 1) {
    const linha = buf.getLine(i);
    if (!linha || !linha.translateToString(true).trim()) continue;
    if (inicio < 0) inicio = i;
    fim = i;
  }
  return inicio < 0 ? null : { inicio, fim };
}

function selecionarTudo(aba) {
  const alvo = aba || abaAtiva;
  if (!alvo || alvo.ehPainel) return;
  const term = alvo.term;
  const faixa = faixaComTexto(term);
  term.clearSelection();
  if (!faixa) {
    piscarAviso("Não há saída pra selecionar");
    return;
  }
  term.select(0, faixa.inicio, (faixa.fim - faixa.inicio + 1) * term.cols);
  term.focus();
  const linhas = faixa.fim - faixa.inicio + 1;
  const soATela = term.buffer.active.type === "alternate";
  piscarAviso(
    linhas + (linhas === 1 ? " linha selecionada" : " linhas selecionadas") +
      (soATela ? ", só o que está na tela" : "") +
      " · Ctrl+C copia, Esc solta"
  );
}

function copiarTudo(aba) {
  const alvo = aba || abaAtiva;
  if (!alvo || alvo.ehPainel) return;
  const antes = alvo.term.hasSelection() ? alvo.term.getSelection() : "";
  let texto = antes;
  if (!texto.trim()) {
    const faixa = faixaComTexto(alvo.term);
    if (faixa) {
      alvo.term.select(0, faixa.inicio, (faixa.fim - faixa.inicio + 1) * alvo.term.cols);
      texto = alvo.term.getSelection();
    }
  }
  alvo.term.clearSelection();
  if (!texto.trim()) {
    piscarAviso("Não há saída pra copiar");
    return;
  }
  navigator.clipboard.writeText(texto);
  const linhas = contarLinhas(texto);
  piscarAviso(linhas + (linhas === 1 ? " linha copiada" : " linhas copiadas"));
}

function limparTela(aba) {
  const alvo = aba || abaAtiva;
  if (!alvo || alvo.ehPainel) return;
  alvo.term.clear();
  alvo.foco();
}

// clicar em 5h, 7d ou no nome da conta abre o painel de uso na conta da aba. O contexto e da
// conversa e o painel nao fala dele, entao o clique nele so mostra o detalhe
el("uso-barra").addEventListener("click", (ev) => {
  const ctx = ev.target.closest("#medidor-ctx");
  if (ctx) {
    if (ctx.title) piscarAviso(ctx.title);
    return;
  }
  if (!ev.target.closest(".medidor, .barra-conta, .modelo")) return;
  const chave = chaveDaAba();
  // a Principal sem leitura leva a onde ela se liga, e nao a uma tela sem limite
  if (ev.target.closest(".medidor") && principalSemLeitura(perfis.find((p) => p.id === chave))) {
    abrirConfig("contas");
    return;
  }
  PainelUso.mostrarConta(contaDoPainel());
});

/* ------------------------------------------------------------------ veu */

// quem recebe o foco quando a caixa abre: o Enter confirma o fechar, mas no remover cai no Cancelar
const FOCO_DO_VEU = {
  "veu-fechar": "#confirmar-fechamento",
  "veu-remover": '[data-fecha="veu-remover"]',
  // o painel inteiro e nao o item do menu: focado por codigo, o item ativo ficava com o anel de
  // foco azul pra sempre, como se o teclado estivesse nele
  "veu-config": ".painel",
};

function abrirVeu(id) {
  const veu = el(id);
  veu.hidden = false;
  // o foco ficava no terminal de tras: o Esc pra cancelar ia pro shell e o Enter pro prompt
  if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
  const alvo = FOCO_DO_VEU[id] ? veu.querySelector(FOCO_DO_VEU[id]) : null;
  if (alvo) alvo.focus();
}

function fecharVeu(id) {
  el(id).hidden = true;
  // a caixa de nova conta fecha por cima das configuracoes, que continuam abertas
  if (!veuAberto() && abaAtiva) abaAtiva.foco();
}

function veuAberto() {
  // a paleta conta como caixa: com ela aberta nenhuma tecla vai pro shell e o Esc so fecha ela
  return ["veu-escolha", "veu-conta", "veu-remover", "veu-config", "veu-fechar", "paleta"].find((id) => !el(id).hidden);
}

document.querySelectorAll("[data-fecha]").forEach((b) => {
  b.addEventListener("click", () => fecharVeu(b.dataset.fecha));
});

function perguntar(titulo, texto, opcoes) {
  /* Caixa de escolha com os botoes que o caso pede. Esc cancela sem fazer nada, e o foco
     cai no botao marcado, pro Enter nao escolher a opcao perigosa sem querer. */
  el("escolha-titulo").textContent = titulo;
  el("escolha-texto").textContent = texto;
  const alvo = el("escolha-botoes");
  alvo.innerHTML = "";
  let focar = null;
  for (const opcao of opcoes) {
    const b = document.createElement("button");
    b.className = "botao" + (opcao.classe ? " " + opcao.classe : "");
    b.textContent = opcao.rotulo;
    b.addEventListener("click", () => {
      fecharVeu("veu-escolha");
      if (opcao.acao) opcao.acao();
    });
    alvo.appendChild(b);
    if (opcao.foco) focar = b;
  }
  abrirVeu("veu-escolha");
  if (focar) focar.focus();
}

// tecla que sozinha nao digita nada: apertar o Ctrl de um atalho nao fecha o menu
const TECLAS_SOLTAS = new Set(["Control", "Shift", "Alt", "AltGraph", "Meta", "CapsLock"]);

function menuAberto() {
  return !elMenu.hidden || !elMenuAba.hidden;
}

function fecharMenus() {
  esconderMenu();
  elMenuAba.hidden = true;
  abaDoMenu = null;
}

/* ----------------------------------------------------------------- menu */

function contaPadraoDoMenu() {
  // a Conta padrao vale pro shell escolhido no menu tambem, igual ao Ctrl+Shift+T
  return perfis.find((p) => p.id === cfg.perfilPadrao) || null;
}

function montarMenu() {
  const alvoShells = el("menu-shells");
  alvoShells.innerHTML = "";
  const padrao = contaPadraoDoMenu();
  for (const s of shells) {
    const b = document.createElement("button");
    b.className = "menu-item";
    // o shell ganha o desenho de um prompt: a bolinha cinza era igual a de conta sem cor
    b.innerHTML =
      '<svg class="menu-icone" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M3.2 4.6 6.4 8l-3.2 3.4M8.4 11.6h4.4"/></svg><span class="nome"></span>';
    b.querySelector(".nome").textContent = s.nome;
    if (padrao) {
      // o selo diz em qual conta a aba vai abrir, pra ninguem gastar o plano errado sem ver
      const selo = document.createElement("span");
      selo.className = "selo";
      selo.textContent = padrao.nome;
      selo.style.color = padrao.cor;
      selo.title = "Conta padrão, em Configurações, Geral";
      b.appendChild(selo);
    }
    b.addEventListener("click", () => {
      esconderMenu();
      novaAba(s.id, padrao ? padrao.id : "");
    });

    const escudo = document.createElement("button");
    escudo.className = "menu-escudo";
    // um escudo em cada linha do menu era ruido: ele mora la, mas so aparece com o mouse em cima
    escudo.innerHTML =
      '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round">' +
      '<path d="M8 1.8 13 3.6v4c0 3.1-2.1 5.4-5 6.6-2.9-1.2-5-3.5-5-6.6v-4z"/></svg><span>Adm</span>';
    escudo.title = elevacao.disponivel
      ? "Abrir este shell com privilégio de administrador, sem conta"
      : TEXTO_DA_ELEVACAO[elevacao.motivo] ||
        "Precisa do sudo, ou do gsudo no Windows, pra abrir elevado na mesma aba";
    // apagado, mas clicavel: botao desabilitado nao recebe o clique, e o motivo so existia
    // num tooltip de botao apagado
    escudo.classList.toggle("indisponivel", !elevacao.disponivel);
    escudo.setAttribute("aria-disabled", elevacao.disponivel ? "false" : "true");
    escudo.addEventListener("click", (ev) => {
      ev.stopPropagation();
      if (!elevacao.disponivel) {
        piscarAviso(escudo.title);
        return;
      }
      esconderMenu();
      novaAba(s.id, "", { elevar: true });
    });
    b.appendChild(escudo);
    alvoShells.appendChild(b);
  }

  const alvoPerfis = el("menu-perfis");
  alvoPerfis.innerHTML = "";
  if (!perfis.length) {
    const vazio = document.createElement("div");
    vazio.className = "menu-item menu-vazio";
    vazio.textContent = "Nenhuma conta cadastrada";
    alvoPerfis.appendChild(vazio);
  }
  if (padrao) {
    // com a conta padrao valendo no shell, quem quer o shell puro precisa de onde escolher
    const semConta = document.createElement("button");
    semConta.className = "menu-item";
    semConta.innerHTML = '<span class="ponto" style="background:#3a3f49"></span><span class="nome"></span>';
    const deSempre = contaPadraoCadastrada();
    semConta.querySelector(".nome").textContent = deSempre ? deSempre.nome : "Principal";
    semConta.title = "Abre o shell padrão na Principal, o login do ~/.claude";
    semConta.addEventListener("click", () => {
      esconderMenu();
      novaAba("", "");
    });
    alvoPerfis.appendChild(semConta);
  }
  // depois de adotada, oferecer de novo so levava ao aviso de que ela ja esta cadastrada
  el("usar-logada").hidden = !!contaPadraoCadastrada();
  for (const p of perfis) {
    const b = document.createElement("button");
    b.className = "menu-item";
    b.innerHTML = '<span class="ponto"></span><span class="nome"></span><span class="selo"></span>';
    b.querySelector(".ponto").style.background = p.cor;
    b.querySelector(".nome").textContent = p.nome;
    b.title = (p.email || p.ferramentaNome) + " · " + p.configDir;
    const selo = b.querySelector(".selo");
    // o grupo lista conta de qualquer CLI; a que nao e do Claude diz de qual e
    const estado = p.logado ? "Logado" : "Sem login";
    selo.textContent = p.ferramenta && p.ferramenta !== "claude" ? p.ferramentaNome + " · " + estado : estado;
    if (p.logado) selo.classList.add("on");
    b.addEventListener("click", () => {
      esconderMenu();
      novaAba("", p.id);
    });
    alvoPerfis.appendChild(b);
  }
}

function recarregarPerfis(depois) {
  bridge.listProfiles((json) => {
    perfis = JSON.parse(json);
    for (const a of ordem) {
      if (a.ehPainel || !a.perfil) continue;
      const atual = perfis.find((p) => p.id === a.perfil.id);
      if (atual) {
        a.perfil = atual;
        a.chaveRefletida = "";
        a.pintar();
      }
    }
    if (depois) depois();
  });
}

function mostrarMenu() {
  recarregarPerfis(() => {
    montarMenu();
    elMenu.hidden = false;
    // o menu nascia num ponto fixo, em cima do proprio botao que o abriu, e nao
    // acompanhava a lateral quando ela era arrastada
    const botao = el("novo").getBoundingClientRect();
    const caixa = elMenu.getBoundingClientRect();
    elMenu.style.top = Math.min(botao.bottom + 4, window.innerHeight - caixa.height - 10) + "px";
    elMenu.style.left = Math.min(botao.left, window.innerWidth - caixa.width - 10) + "px";
  });
}

function esconderMenu() {
  elMenu.hidden = true;
}

el("novo").addEventListener("click", (ev) => {
  ev.stopPropagation();
  if (elMenu.hidden) mostrarMenu();
  else esconderMenu();
});

document.addEventListener("mousedown", (ev) => {
  if (!elMenu.hidden && !elMenu.contains(ev.target) && ev.target.id !== "novo") esconderMenu();
});

const TEXTO_DA_ELEVACAO = {
  desligado: "O sudo do Windows está desligado. Ligue em Configurações, Sistema, Para desenvolvedores.",
  // no modo de fabrica o sudo abre um console separado, fora da aba, que fechava com codigo 0
  "nova janela":
    "O sudo do Windows está no modo que abre outra janela. Em Configurações, Sistema, Para desenvolvedores, escolha o modo que roda na mesma janela.",
  "sem entrada":
    "O sudo do Windows está no modo sem teclado. Em Configurações, Sistema, Para desenvolvedores, escolha o modo que roda na mesma janela.",
  verificando: "Ainda conferindo se o sudo abre na mesma aba. Tente de novo em um instante.",
};

function receberElevacao(jsonElev) {
  try {
    elevacao = JSON.parse(jsonElev);
  } catch (e) {
    elevacao = { disponivel: false, motivo: "sem sudo" };
  }
}

/* ---------------------------------------------------------- nova conta */

function montarSelecaoFerramentas() {
  const sel = el("ferramenta-conta");
  sel.innerHTML = "";
  for (const f of ferramentas) {
    const o = document.createElement("option");
    o.value = f.id;
    o.textContent = f.nome;
    sel.appendChild(o);
  }
  atualizarDicaFerramenta();
}

function atualizarDicaFerramenta() {
  const f = ferramentas.find((x) => x.id === el("ferramenta-conta").value);
  const dica = el("dica-ferramenta");
  // conta de CLI que nao esta instalado so mostrava o erro do PowerShell na aba nova
  dica.textContent = !f
    ? ""
    : f.achado === false
    ? "O comando " + (f.comando || f.nome) + " não foi encontrado nesta máquina. Instale antes de usar esta conta."
    : "Login separado, guardado numa pasta só desta conta";
  dica.classList.toggle("alerta", !!f && f.achado === false);
}

el("ferramenta-conta").addEventListener("change", atualizarDicaFerramenta);

function pedirNovaConta() {
  esconderMenu();
  montarSelecaoFerramentas();
  abrirVeu("veu-conta");
  el("nome-conta").value = "";
  el("nome-conta").classList.remove("campo-erro");
  el("nome-conta").focus();
}

function adotarPrincipal(depois) {
  bridge.adoptMainAccount("Principal", (json) => {
    let resposta = {};
    try {
      resposta = JSON.parse(json);
    } catch (e) {
      resposta = {};
    }
    if (resposta.erro) {
      piscarAviso("A Principal já está cadastrada como " + resposta.nome);
      recarregarPerfis(() => depois && depois(resposta.id));
      return;
    }
    recarregarPerfis(() => {
      montarContas();
      montarSelecaoPerfis();
      piscarAviso("Principal cadastrada com o login que já existia");
      if (depois) depois(resposta.id);
    });
  });
}

el("adotar-principal").addEventListener("click", () => adotarPrincipal(null));

el("usar-logada").addEventListener("click", () => {
  esconderMenu();
  adotarPrincipal((id) => {
    if (id) novaAba("", id);
  });
});

el("nova-conta").addEventListener("click", pedirNovaConta);
el("nova-conta-config").addEventListener("click", pedirNovaConta);

function confirmarConta() {
  const campo = el("nome-conta");
  const nome = campo.value.trim();
  // Criar com o apelido vazio nao fazia nada e nao dizia nada; apelido repetido deixava
  // duas contas iguais no menu, no filtro de Uso e na barra
  const repetida = perfis.find((p) => p.nome.trim().toLowerCase() === nome.toLowerCase());
  if (!nome || repetida) {
    campo.classList.add("campo-erro");
    campo.focus();
    piscarAviso(nome ? "Já existe uma conta chamada " + repetida.nome : "Dê um apelido pra conta");
    return;
  }
  // A conta nasce sem login, e a caixa prometia o login na primeira vez que o CLI rodasse. Pelas
  // Configuracoes nao abria aba nenhuma, e pelo menu, com a abertura automatica desligada, subia
  // um shell parado. Agora os dois abrem a aba que ja faz o login, como o botao Entrar.
  bridge.createProfile(nome, el("ferramenta-conta").value, () => {
    recarregarPerfis(() => {
      fecharVeu("veu-conta");
      if (!el("veu-config").hidden) fecharVeu("veu-config");
      montarContas();
      montarSelecaoPerfis();
      const criada = perfis.find((p) => p.nome === nome) || perfis[perfis.length - 1];
      if (!criada) return;
      novaAba("", criada.id, { entrar: true });
      piscarAviso("Siga o login que abriu na aba");
    });
  });
}

el("confirmar-conta").addEventListener("click", confirmarConta);
el("nome-conta").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") confirmarConta();
});
el("nome-conta").addEventListener("input", () => el("nome-conta").classList.remove("campo-erro"));

/* ------------------------------------------------------------- remocao */

function textoDeAbas(quantas) {
  return quantas === 1 ? "1 aba aberta" : quantas + " abas abertas";
}

function pedirRemocao(perfil) {
  removendo = perfil;
  el("remover-nome").textContent = perfil.nome;
  const abertas = abasDaConta(perfil.id).length;
  let texto = perfil.emprestada
    ? "Essa conta aponta pra pasta de login que já existia antes do app. Remover tira ela só do cadastro daqui; o login continua onde sempre esteve."
    : perfil.logado
    ? "Essa conta está logada nesta máquina. Sem marcar a opção abaixo, o login continua no disco e você pode recadastrar depois sem precisar entrar de novo."
    : "Essa conta ainda não tem login nesta máquina.";
  // as abas continuam rodando na pasta da conta; apagar o login com elas abertas deixava a
  // pasta pela metade, porque o Windows nao apaga arquivo em uso
  if (abertas) {
    texto +=
      " Ela tem " + textoDeAbas(abertas) + (abertas === 1 ? ", que continua" : ", que continuam") +
      " rodando até você fechar" + (perfil.emprestada ? "." : "; pra apagar o login, feche antes.");
  }
  el("remover-texto").textContent = texto;
  el("remover-login").checked = false;
  el("remover-login").disabled = abertas > 0;
  el("remover-login").closest(".marca-caixa").hidden = !!perfil.emprestada;
  abrirVeu("veu-remover");
}

el("confirmar-remocao").addEventListener("click", () => {
  if (!removendo) return;
  bridge.removeProfile(removendo.id, el("remover-login").checked && !el("remover-login").disabled);
  removendo = null;
  recarregarPerfis(() => {
    montarContas();
    montarSelecaoPerfis();
    fecharVeu("veu-remover");
  });
});

/* ------------------------------------------------------------- contas */

function tamanho(bytes) {
  if (!bytes) return "0 B";
  if (bytes >= 1048576) return (bytes / 1048576).toFixed(1) + " MB";
  if (bytes >= 1024) return Math.round(bytes / 1024) + " kB";
  return bytes + " B";
}

function contaMudou() {
  /* Nome e cor novos valem na hora na barra, nos cartoes, nos chapeus da grade e na Conta
     padrao. Antes ficavam os antigos por ate 30 s, ou ate reabrir. */
  montarContas();
  montarSelecaoPerfis();
  for (const a of ordem) {
    if (a.ehPainel) continue;
    a.chaveRefletida = "";
    if (a.sessao) a.refletir(a.sessao);
    else a.pintar();
  }
  pintarUsoDaBarra();
  if (emGrade) pintarGrade();
  else if (divisao) pintarDivisao();
}

let leituraPrincipal = null;

function montarLeituraPrincipal() {
  /* A conta de sempre so se liga a aba e ao limite passando pelo coletor, e o app nao mexe no
     settings dela sozinho. Aqui a pessoa liga, sabendo o que muda, e desliga quando quiser. */
  const botao = el("leitura-principal");
  const texto = el("leitura-principal-texto");
  if (!bridge) return;
  bridge.leituraPrincipal((ligada) => {
    leituraPrincipal = !!ligada;
    botao.textContent = ligada ? "Desligar" : "Ligar";
    botao.classList.toggle("botao-forte", !ligada);
    texto.textContent = ligada
      ? "Ligado. A aba da Principal mostra o estado do Claude e o limite do plano. A sua status line de antes continua sendo chamada."
      : "A conta já logada, do ~/.claude, ainda não passa pelo Loom: a aba dela não mostra o estado do Claude e o limite fica sem leitura.";
    pintarUsoDaBarra();
  });
}

el("leitura-principal").addEventListener("click", () => {
  if (leituraPrincipal) {
    bridge.desligarLeituraPrincipal((ok) => {
      piscarAviso(ok ? "Pronto, o settings.json da Principal voltou como era" : "Não havia nada do Loom no settings.json da Principal");
      montarLeituraPrincipal();
    });
    return;
  }
  perguntar(
    "Acompanhar a Principal no Loom",
    "Isso edita o settings.json da pasta ~/.claude: a status line passa pelo Loom, que chama a sua de " +
      "antes em seguida, e entram dois ganchos que marcam a conversa com a aba. Vale também pro Claude " +
      "aberto fora do Loom. Se o Loom sumir, a sua status line volta sozinha, e Desligar desfaz tudo.",
    [
      { rotulo: "Cancelar", foco: true },
      {
        rotulo: "Ligar",
        classe: "botao-forte",
        acao: () =>
          bridge.ligarLeituraPrincipal((ok) => {
            piscarAviso(
              ok
                ? "Pronto. Vale pras conversas da Principal abertas a partir de agora"
                : "Não consegui editar o settings.json da Principal. Confira se ele é um JSON válido"
            );
            montarLeituraPrincipal();
          }),
      },
    ]
  );
});

function montarContas() {
  const alvo = el("lista-contas");
  alvo.innerHTML = "";
  el("adotar-principal").hidden = !!contaPadraoCadastrada();
  montarLeituraPrincipal();
  if (!perfis.length) {
    const vazio = document.createElement("p");
    vazio.className = "ajuda";
    vazio.textContent = "Nenhuma conta cadastrada ainda.";
    alvo.appendChild(vazio);
    return;
  }
  for (const p of perfis) {
    const linha = document.createElement("div");
    linha.className = "conta-linha";

    const cor = document.createElement("button");
    cor.className = "conta-cor";
    cor.style.background = p.cor;
    cor.title = "Trocar a cor";
    cor.addEventListener("click", () => {
      const paleta = ["#4f9cf9", "#63c98a", "#d9b25f", "#b088f0", "#5fc2c9", "#f08189"];
      const proxima = paleta[(paleta.indexOf(p.cor) + 1) % paleta.length];
      bridge.updateProfile(p.id, "", proxima);
      recarregarPerfis(contaMudou);
    });

    const nome = document.createElement("input");
    nome.className = "conta-nome";
    nome.value = p.nome;
    nome.title = p.nome;
    nome.spellcheck = false;
    nome.addEventListener("blur", () => {
      const novo = nome.value.trim();
      // vazio nao grava e a linha ficava em branco ate reabrir; repetido deixava duas contas
      // iguais no menu, no filtro de Uso e na barra, o que o Criar ja barrava
      if (!novo) {
        nome.value = p.nome;
        nome.classList.remove("campo-erro");
        return;
      }
      if (novo === p.nome) return;
      const repetida = perfis.find((o) => o.id !== p.id && o.nome.trim().toLowerCase() === novo.toLowerCase());
      if (repetida) {
        nome.classList.add("campo-erro");
        piscarAviso("Já existe uma conta chamada " + repetida.nome);
        return;
      }
      nome.classList.remove("campo-erro");
      bridge.updateProfile(p.id, novo, "");
      recarregarPerfis(contaMudou);
    });
    nome.addEventListener("input", () => nome.classList.remove("campo-erro"));
    nome.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") nome.blur();
      else if (ev.key === "Escape") {
        // o Esc desiste do nome, em vez de fechar as configuracoes com ele pela metade
        ev.stopPropagation();
        nome.value = p.nome;
        nome.blur();
      }
    });

    const cli = document.createElement("span");
    cli.className = "conta-info conta-email";
    cli.textContent = p.email || p.ferramentaNome;
    cli.title = p.email ? p.ferramentaNome + " · " + p.configDir : p.configDir;

    const info = document.createElement("span");
    info.className = "conta-info" + (p.logado ? " on" : "");
    info.textContent = p.emprestada ? "Principal · login do ~/.claude" : p.logado ? "Logado" : "Sem login";
    info.title = p.configDir;
    // o tamanho em disco custa varrer milhares de arquivos, entao ele nao vem no
    // listProfiles: e pedido so aqui, onde de fato aparece
    if (p.logado && !p.emprestada) {
      bridge.profileSize(p.id, (bytes) => {
        info.textContent = "Logado · " + tamanho(bytes);
      });
    }

    let entrar = null;
    if (!p.logado && !p.emprestada) {
      entrar = document.createElement("button");
      entrar.className = "conta-acao entrar";
      entrar.textContent = "Entrar";
      entrar.title = "Abre uma aba nesta conta e já começa o login";
      entrar.addEventListener("click", () => {
        const ferramenta = ferramentas.find((f) => f.id === p.ferramenta);
        if (ferramenta && !ferramenta.comando) {
          piscarAviso("Essa ferramenta não tem comando cadastrado. Preencha em Ferramentas.");
          return;
        }
        fecharVeu("veu-config");
        novaAba("", p.id, { entrar: true });
        piscarAviso("Siga o login que abriu na aba");
      });
    }

    const esquecer = document.createElement("button");
    esquecer.className = "conta-acao";
    esquecer.textContent = "Deslogar";
    esquecer.style.opacity = p.logado && !p.emprestada ? "1" : ".4";
    esquecer.title = "Apaga só a credencial, mantendo histórico e ajustes";
    let confirmando = null;
    esquecer.addEventListener("click", () => {
      if (!p.logado || p.emprestada) {
        if (p.emprestada) piscarAviso("Essa conta usa a pasta de login que já existia, o app não mexe nela");
        return;
      }
      const abertas = abasDaConta(p.id).length;
      // apagava a credencial no primeiro clique, ao lado do Remover que pede confirmacao;
      // agora o primeiro clique arma e o segundo, em ate 3 segundos, apaga
      if (!confirmando) {
        esquecer.textContent = "Confirmar";
        esquecer.classList.add("armado");
        esquecer.title = abertas
          ? "Clique de novo pra apagar o login. " + textoDeAbas(abertas) + " nesta conta " +
            (abertas === 1 ? "perde" : "perdem") + " o acesso."
          : "Clique de novo pra apagar o login desta conta";
        confirmando = setTimeout(() => {
          confirmando = null;
          esquecer.textContent = "Deslogar";
          esquecer.classList.remove("armado");
          esquecer.title = "Apaga só a credencial, mantendo histórico e ajustes";
        }, 3000);
        return;
      }
      clearTimeout(confirmando);
      confirmando = null;
      bridge.forgetLogin(p.id, () => {
        if (abertas) {
          piscarAviso(
            "Login apagado. " + textoDeAbas(abertas) + " nesta conta " +
              (abertas === 1 ? "continua" : "continuam") + " rodando sem ele"
          );
        }
        // a barra e os chapeus da grade seguiam com o limite da conta que perdeu o login
        recarregarPerfis(contaMudou);
      });
    });

    const apagar = document.createElement("button");
    apagar.className = "conta-acao perigo";
    apagar.textContent = "Remover";
    apagar.addEventListener("click", () => pedirRemocao(p));

    // nome em cima e e-mail embaixo, com o espaco que sobrar: lado a lado, o nome ficava
    // preso em 120px e o e-mail virava tres letras
    const identidade = document.createElement("div");
    identidade.className = "conta-id";
    identidade.append(nome, cli);
    linha.append(cor, identidade, info);
    if (entrar) linha.appendChild(entrar);
    else linha.appendChild(esquecer);
    linha.appendChild(apagar);
    alvo.appendChild(linha);
  }
}

/* -------------------------------------------------------- ferramentas */

function montarFerramentas() {
  const alvo = el("lista-ferramentas");
  alvo.innerHTML = "";
  for (const f of ferramentas) {
    const linha = document.createElement("div");
    linha.className = "ferramenta-linha";

    const titulo = document.createElement("div");
    titulo.className = "titulo";
    titulo.textContent = f.nome;
    // conferido no PATH desta maquina, em vez da frase fixa que dizia confirmado em qualquer uma
    const achado = document.createElement("span");
    achado.className = "ferramenta-achada" + (f.achado ? " on" : "");
    achado.textContent = f.achado ? "Encontrado" : "Não encontrado";
    achado.title = f.achado
      ? "O comando " + f.comando + " existe nesta máquina"
      : "O comando " + (f.comando || "desta ferramenta") + " não foi achado no PATH desta máquina";
    titulo.appendChild(achado);
    linha.appendChild(titulo);

    for (const [chave, rotulo] of [["comando", "Comando"], ["env", "Variável de ambiente"], ["login", "Arquivo de login"]]) {
      const bloco = document.createElement("div");
      const rot = document.createElement("label");
      rot.textContent = rotulo;
      const campo = document.createElement("input");
      campo.className = "campo";
      campo.value = f[chave] || "";
      campo.spellcheck = false;
      campo.addEventListener("change", () => {
        const dados = { id: f.id, nome: f.nome, comando: f.comando, env: f.env, login: f.login };
        dados[chave] = campo.value.trim();
        bridge.saveTool(JSON.stringify(dados), () => carregarFerramentas(montarFerramentas));
      });
      bloco.append(rot, campo);
      linha.appendChild(bloco);
    }
    alvo.appendChild(linha);
  }
}

function carregarFerramentas(depois) {
  bridge.listTools((json) => {
    ferramentas = JSON.parse(json);
    if (depois) depois();
  });
}

/* ------------------------------------------------------- configuracoes */

const CAMPOS = [
  ["fonte", "select"],
  ["tamanhoFonte", "range"],
  ["entrelinha", "range"],
  ["espacoLetra", "range"],
  ["cursor", "select"],
  ["cursorPisca", "check"],
  ["densidade", "select"],
  ["barraDoSistema", "check"],
  ["mostrarRecursos", "check"],
  ["desenho", "select"],
  ["shellPadrao", "select"],
  ["perfilPadrao", "select"],
  ["historico", "number"],
  ["confirmarFechar", "check"],
  ["abrirCliDaConta", "check"],
  ["mostrarUsoNaBarra", "check"],
  ["confiarPastas", "check"],
  ["restaurarAbas", "check"],
  ["guardarHistorico", "check"],
  ["retomarConversas", "check"],
  ["esconderStatusLine", "check"],
  ["avisarQuandoPronta", "check"],
  ["avisoDeLimite", "check"],
  ["fecharAoSair", "check"],
  ["editor", "text"],
];
const PADRAO_LIGADO = new Set(["guardarHistorico", "retomarConversas"]);

function montarSelecaoPerfis() {
  const sel = el("cfg-perfilPadrao");
  // "Nenhuma, shell puro" enganava: a aba sem conta abre o Claude da conta ja logada
  const deSempre = contaPadraoCadastrada();
  sel.innerHTML = "";
  const nenhuma = document.createElement("option");
  nenhuma.value = "";
  nenhuma.textContent = deSempre ? "Sem conta, abre na " + deSempre.nome : "Principal, do ~/.claude";
  sel.appendChild(nenhuma);
  for (const p of perfis) {
    const o = document.createElement("option");
    o.value = p.id;
    o.textContent = p.nome;
    sel.appendChild(o);
  }
  sel.value = perfis.some((p) => p.id === cfg.perfilPadrao) ? cfg.perfilPadrao : "";
}

function montarSelecaoShells() {
  const sel = el("cfg-shellPadrao");
  sel.innerHTML = "";
  for (const s of shells) {
    const o = document.createElement("option");
    o.value = s.id;
    o.textContent = s.nome;
    sel.appendChild(o);
  }
  sel.value = cfg.shellPadrao || (shells.length ? shells[0].id : "");
}

function preencherCampos() {
  for (const [chave, tipo] of CAMPOS) {
    const campo = el("cfg-" + chave);
    if (!campo) continue;
    // chave nova que o config de antes nao tem nasce ligada, como o padrao do app
    if (tipo === "check") campo.checked = PADRAO_LIGADO.has(chave) && cfg[chave] === undefined ? true : !!cfg[chave];
    else campo.value = cfg[chave];
    // valor que nao e opcao nenhuma, tipo o shell padrao vazio de fabrica ou uma conta removida,
    // deixava o campo em branco; o primeiro e o que o app usa nesse caso
    if (tipo === "select" && campo.selectedIndex < 0) campo.selectedIndex = 0;
    const saida = el("out-" + chave);
    if (saida) saida.textContent = cfg[chave];
  }
  document.documentElement.style.setProperty("--mono", pilhaDeFonte(cfg.fonte));
  document.documentElement.dataset.densidade = cfg.densidade || "confortavel";
  const amostra = el("amostra");
  amostra.style.fontSize = cfg.tamanhoFonte + "px";
  amostra.style.lineHeight = cfg.entrelinha;
  amostra.style.letterSpacing = cfg.espacoLetra + "px";
  // guardar a tela e retomar a conversa so existem com as abas voltando: sem elas, apagam
  document.querySelectorAll("[data-depende]").forEach((linha) => {
    const desligada = cfg[linha.dataset.depende] === false;
    linha.classList.toggle("desligada", desligada);
    const entrada = linha.querySelector("input, select");
    if (entrada) entrada.disabled = desligada;
  });
}

function aplicarTudo() {
  for (const a of ordem) a.aplicar();
  preencherCampos();
  pintarUsoDaBarra();
  PainelUso.aplicar();
  aplicarRecursos();
}

function ligarCampos() {
  for (const [chave, tipo] of CAMPOS) {
    const campo = el("cfg-" + chave);
    if (!campo) continue;
    let atrasar = null;
    campo.addEventListener(tipo === "range" ? "input" : "change", () => {
      let valor;
      if (tipo === "check") valor = campo.checked;
      else if (tipo === "number") {
        // Apagar o numero e clicar fora gravava 0, e o historico 0 cortava na hora a rolagem
        // de todas as abas, sem volta. Invalido volta ao que era; fora da faixa, vai pra borda.
        const lido = parseInt(campo.value, 10);
        if (!Number.isFinite(lido)) {
          campo.value = cfg[chave];
          return;
        }
        const menor = campo.min === "" ? -Infinity : Number(campo.min);
        const maior = campo.max === "" ? Infinity : Number(campo.max);
        valor = Math.min(Math.max(lido, menor), maior);
        campo.value = valor;
      } else if (tipo === "range") valor = chave === "entrelinha" ? parseFloat(campo.value) : parseInt(campo.value, 10);
      else valor = campo.value;
      cfg[chave] = valor;
      // o tamanho escolhido aqui vale como esta, sem o zoom do Ctrl somado por cima
      if (chave === "tamanhoFonte") zoomAtual = 0;
      const saida = el("out-" + chave);
      if (saida) saida.textContent = valor;
      // arrastando o cursor, cada pixel gravava em disco e remedia todos os terminais.
      // A tela responde na hora; o disco espera a mao parar.
      clearTimeout(atrasar);
      atrasar = setTimeout(
        () => {
          aplicarTudo();
          bridge.saveConfig(JSON.stringify({ [chave]: valor }), (json) => {
            cfg = JSON.parse(json);
            // o motor so troca na proxima subida: a linha diz o que vale agora e o que vem
            if (chave === "desenho") bridge.desenho(receberDesenho);
          });
        },
        tipo === "range" ? 90 : 0
      );
    });
  }
}

document.querySelectorAll(".nav-item").forEach((b) => {
  b.addEventListener("click", () => mostrarSecao(b.dataset.alvo));
});

function mostrarSecao(alvo) {
  // secao que nao existe mais, como a Terminal e a Uso de antes, cai na Geral
  const existe = !!el("sec-" + alvo);
  const escolhida = existe ? alvo : "geral";
  document.querySelectorAll(".nav-item").forEach((o) => o.classList.toggle("ativa", o.dataset.alvo === escolhida));
  document.querySelectorAll(".secao").forEach((s) => s.classList.toggle("ativa", s.id === "sec-" + escolhida));
  const corpo = document.querySelector(".painel-corpo");
  if (corpo) corpo.scrollTop = 0;
}

function abrirConfig(secao) {
  esconderMenu();
  carregarFerramentas(() => {
    recarregarPerfis(() => {
      montarContas();
      montarFerramentas();
      montarSelecaoPerfis();
      montarSelecaoShells();
      preencherCampos();
      // a tela pode ter caido depois que a pagina subiu, e ai a proxima subida ja e outra
      bridge.desenho(receberDesenho);
      if (secao) mostrarSecao(secao);
      abrirVeu("veu-config");
    });
  });
}

/* -------------------------------------------------------------- desenho */

function textoDoDesenho(d) {
  /* O que a linha de Desenho diz. O motor so troca quando o Loom abre de novo, entao o texto
     separa o que esta valendo agora do que vale na proxima vez, e diz por que. */
  const nomes = { gpu: "GPU", software: "software" };
  const em = d.quedaEm ? " em " + d.quedaEm : "";
  let aviso = "";
  if (d.escolha === "auto" && d.porQueda) {
    aviso = "O Loom passou a desenhar sem a placa de vídeo depois de uma queda da tela" + em + ".";
    if (d.proxima === "software") aviso += d.ateEm ? " Volta sozinho à placa a partir de " + d.ateEm + "." : "";
    else aviso += " Na próxima vez que abrir, ele volta à placa de vídeo.";
  } else if (d.escolha === "auto" && d.proxima === "software") {
    aviso = "A tela caiu" + em + ". Na próxima vez que abrir, o Loom desenha sem a placa de vídeo por 7 dias.";
  }
  let agora = d.emUso === "software" ? "Agora sem a placa de vídeo, desenhando por software." : "Agora com a placa de vídeo.";
  if (d.porFora) agora += " Quem manda nisso é uma variável de ambiente definida fora do Loom.";
  else if (!aviso && d.proxima && d.proxima !== d.emUso) agora += " Na próxima vez que abrir: " + nomes[d.proxima] + ".";
  return { agora, aviso, voltar: d.escolha === "auto" && d.proxima === "software" };
}

function receberDesenho(json) {
  try {
    desenho = JSON.parse(json);
  } catch (e) {
    return;
  }
  const texto = textoDoDesenho(desenho);
  el("desenho-agora").textContent = texto.agora;
  el("desenho-aviso").textContent = texto.aviso;
  el("aviso-desenho").hidden = !texto.aviso;
  el("voltar-gpu").hidden = !texto.voltar;
}

el("voltar-gpu").addEventListener("click", () => {
  bridge.voltarParaGpu((json) => {
    receberDesenho(json);
    piscarAviso("Pronto. Na próxima vez que abrir, o Loom volta à placa de vídeo");
  });
});

el("abrir-config").addEventListener("click", () => abrirConfig());
el("vazio-nova").addEventListener("click", abaPadrao);

/* ------------------------------------------------------------- sessoes */

const elLateral = el("lateral");
const elListaSessoes = el("abas");
let sessoes = [];

function tempoCurto(s) {
  if (s < 60) return s + "s";
  if (s < 3600) return Math.round(s / 60) + "min";
  if (s < 86400) return Math.round(s / 3600) + "h";
  return Math.round(s / 86400) + "d";
}

const ESTADO = {
  pensando: { texto: "thinking", dica: "O modelo está gerando a resposta agora" },
  rodando: { texto: "working", dica: "Uma ferramenta está em execução" },
  aguardando: { texto: "ready", dica: "Terminou e está esperando você digitar" },
  // stopped parecia processo parado, com o shell e o Claude vivos no prompt
  parada: { texto: "idle", dica: "Sem nada escrito há bastante tempo" },
};

// ready por um motivo que nao e ter terminado: a pessoa precisa responder pra ele seguir. Em
// ingles como os estados, pra linha nao sair "ready permissão" com as duas linguas juntas
const ESPERA = {
  pergunta: { texto: "question", dica: "Parou pra te perguntar, ou pra você aprovar o plano" },
  permissao: { texto: "permission", dica: "Parou pra pedir sua permissão" },
};

function motivoDaEspera(s) {
  /* O que a sessao pronta espera de voce, com a ferramenta quando e permissao. Vazio quando ela
     so terminou de responder. */
  const espera = s && s.estado === "aguardando" ? ESPERA[s.espera] : null;
  if (!espera) return "";
  return espera.texto + (s.espera === "permissao" && s.ferramenta ? " " + s.ferramenta : "");
}

function textoDoEstado(s) {
  // no topo do painel da grade dizia so READY, igual a um que so terminou
  const base = (ESTADO[s.estado] || {}).texto || s.estado;
  const motivo = motivoDaEspera(s);
  return motivo ? base + " " + motivo : base;
}

function dicaDoEstado(s) {
  const espera = s.estado === "aguardando" ? ESPERA[s.espera] : null;
  return espera ? espera.dica : (ESTADO[s.estado] || {}).dica || "";
}

// a conversa cujo terminal ja fechou: o Claude dela morreu junto com o shell
const ENCERRADA = {
  texto: "closed",
  dica: "A aba desta conversa foi fechada. Clique pra ver as opções; Retomar volta a conversa numa aba.",
};

function pintarLinhaDeEstado(linha, s, encerrada) {
  /* A linha de estado do cartao: o estado, a ferramenta que roda ou o que a sessao espera. */
  linha.innerHTML = "";
  if (encerrada) {
    linha.textContent = ENCERRADA.texto;
    linha.title = ENCERRADA.dica;
    return;
  }
  const texto = motivoDaEspera(s);
  if (texto) {
    const motivo = document.createElement("span");
    motivo.className = "ferramenta";
    motivo.textContent = texto;
    linha.append(document.createTextNode(ESTADO.aguardando.texto), motivo);
    linha.title = dicaDoEstado(s);
    return;
  }
  if (s.estado === "rodando" && s.ferramenta) {
    const f = document.createElement("span");
    f.className = "ferramenta";
    f.textContent = s.ferramenta;
    const alvo = document.createElement("span");
    alvo.textContent = s.alvo || "";
    linha.append(f, alvo);
  } else {
    linha.textContent = (ESTADO[s.estado] || {}).texto || s.estado;
  }
  linha.title = (ESTADO[s.estado] || {}).dica || "";
}

// quantos segundos sem saida no terminal ainda contam como o Claude desenhando o contador
const SAIDA_RECENTE_MS = 6000;
// o que a ponta do transcript diz quando o Claude ainda esta no meio do turno
const TRABALHO = new Set(["rodando", "pensando"]);

function teveSaidaRecente(aba) {
  return !!aba && !!aba.ultimaSaida && Date.now() - aba.ultimaSaida < SAIDA_RECENTE_MS;
}

function comoNaAba(s, aba) {
  /* O estado que o cartao de uma aba viva mostra. O transcript nao diz tudo: numa ferramenta
     longa ele fica calado, e passado um minuto e meio a sessao virava ready e saia o aviso de
     que terminou, com o Claude ainda trabalhando. O terminal da aba sabe mais, porque o Claude
     redesenha o contador enquanto trabalha. E quem terminou e esta esperando continua ready
     mesmo depois de 20 minutos, em vez de desbotar como abandonada. */
  if (!s) return s;
  if (TRABALHO.has(s.bruto) && s.estado !== s.bruto && teveSaidaRecente(aba)) {
    return Object.assign({}, s, { estado: s.bruto, espera: "" });
  }
  // a pergunta sem resposta tambem continua esperando, como a resposta sem leitura
  if (s.estado === "parada" && (s.bruto === "respondeu" || s.bruto === "pergunta")) {
    return Object.assign({}, s, { estado: "aguardando" });
  }
  return s;
}

let pastaPessoal = "";

function contaDaSessao(s) {
  /* A pasta onde o transcript esta e a verdade sobre de quem e a sessao. A marca gravada
     na hora pode ser antiga, de antes de a conta ser renomeada ou cadastrada. */
  if (s.perfil && s.perfil !== "__padrao__") return s.perfil;
  // o Codex da pasta padrao nao e da conta de sempre do Claude
  if (s.cli === "codex") return "";
  const cadastrada = contaPadraoCadastrada();
  if (cadastrada) return cadastrada.id;
  return s.conta || s.perfil;
}

function nomeDaPasta(caminho) {
  /* A pasta pessoal se chama como voce, entao mostrar o nome dela vira confusao.
     Todo terminal escreve ~ nesse caso, e e o que a pessoa reconhece na hora. */
  if (!caminho) return "";
  const limpo = caminho.replace(/[\\/]+$/, "");
  if (pastaPessoal && limpo.toLowerCase() === pastaPessoal.toLowerCase()) return "~";
  const partes = limpo.split(/[\\/]/).filter(Boolean);
  return partes.length ? partes[partes.length - 1] : "";
}

function tituloDaConversa(s, ferramenta) {
  /* Conversa ainda sem titulo lia "Claude Code" em cima e "Claude Code · pasta" embaixo, e tres
     delas viravam tres cartoes iguais. Sem titulo, o nome da pasta diz qual e qual. */
  // o projeto vem pronto do Codex Desktop, que roda cada chat numa pasta de rascunho com data
  return s.titulo || s.projeto || nomeDaPasta(s.pasta) || ferramenta;
}

function pintarOrigem(cartao, ferramenta, s, titulo) {
  /* Abaixo do titulo, quem gerou a conversa e onde ela roda. Quando o titulo ja e a pasta, so
     a ferramenta, pra nao repetir. */
  const alvo = cartao.querySelector(".sessao-origem");
  if (!alvo) return;
  alvo.innerHTML = "<b></b><i></i>";
  alvo.querySelector("b").textContent = ferramenta;
  const pasta = s.projeto || nomeDaPasta(s.pasta);
  alvo.querySelector("i").textContent = pasta && pasta !== titulo ? " · " + pasta : "";
  alvo.title = s.pasta || "";
}

function barraContexto(valor) {
  const ctx = document.createElement("span");
  ctx.className = "sessao-ctx";
  if (valor >= 85) ctx.classList.add("critico");
  else if (valor >= 60) ctx.classList.add("atencao");
  ctx.innerHTML = '<span class="ctx-trilho"><b></b></span><em></em>';
  ctx.querySelector("b").style.width = Math.min(valor, 100) + "%";
  ctx.querySelector("em").textContent = Math.round(valor) + "%";
  ctx.title = "janela de contexto desta sessão";
  return ctx;
}

function atualizarVazio() {
  /* Sem nenhuma aba a janela ficava um retangulo cinza sem uma palavra. */
  const alvo = el("palco-vazio");
  if (alvo) alvo.hidden = ordem.some((a) => !a.ehPainel);
}

function montarSessoes() {
  const contador = el("conta-sessoes");
  const terminais = ordem.filter((a) => !a.ehPainel);
  if (contador) contador.textContent = terminais.length ? String(terminais.length) : "";
  atualizarVazio();

  // Pareamento pela marca exata: o Claude que roda numa aba herda o id dela e o coletor grava.
  // So aba viva entra: a de shell encerrado continuava ready e com a idade andando, e quem
  // clicava achava um terminal morto. A antiga segunda passada, por conta, colava numa aba
  // recem restaurada qualquer conversa da mesma conta com menos de 20 minutos, e o cartao do
  // shell novo nascia com o titulo e o ready de uma conversa que morreu com o app.
  const usadas = new Set();
  const donos = new Map();

  for (const aba of terminais) {
    if (!aba.viva) continue;
    // Saindo do Claude com /exit ou Ctrl+C o shell fica, e a aba seguia com o titulo, o ready
    // e o contexto da conversa encerrada. Sem programa rodando no shell, nao ha Claude nela.
    // A elevada fica como era: o que roda nela esta fora do alcance da consulta.
    aba.programaVivo = !abasComPrograma || aba.elevado || abasComPrograma.has(aba.sessionId);
    const daqui = sessoes.filter((s) => !usadas.has(s.sessao) && s.aba && s.aba === aba.sessionId);
    if (!daqui.length) continue;
    // a mais recente e a de agora: depois de um /clear, a velha, pronta, vinha antes na lista
    const recente = daqui.reduce((a, b) => (b.idade < a.idade ? b : a));
    if (aba.programaVivo) {
      usadas.add(recente.sessao);
      donos.set(aba, comoNaAba(recente, aba));
    } else {
      // a que rodou aqui fica no botao direito, como a de antes de reabrir o app
      if (aba.conversaAnterior !== recente.sessao) {
        aba.conversaAnterior = recente.sessao;
        aba.tituloAnterior = recente.titulo || "";
        aba.chaveRefletida = "";
      }
    }
  }

  for (const aba of terminais) aba.refletir(aba.viva ? donos.get(aba) || null : null);
  // depois do refletir: e aqui que a aba passa a saber em que estado ela esta
  avisarProntas(terminais);
  anotarAbas();

  if (emGrade) pintarGrade();
  else if (divisao) pintarDivisao();

  // o contexto e o modelo da barra saem da aba pareada: saindo do Claude, eles saem junto
  pintarUsoDaBarra();
}

function abasDaConta(conta) {
  return ordem.filter((a) => !a.ehPainel && a.viva && a.perfil && a.perfil.id === conta);
}

function abaDaSessao(s) {
  /* So o pareamento vale. Pela marca da aba, uma conversa antiga dela, de antes de um /clear
     ou de sair do Claude, devolvia a aba viva: o Encerrar fechava a conversa atual, e a barra
     podia mostrar o modelo e o contexto da velha. */
  for (const aba of ordem) {
    if (!aba.ehPainel && aba.sessao && aba.sessao.sessao === s.sessao) return aba;
  }
  return null;
}

let avisoRelogio = null;

function tempoDoAviso(texto) {
  // 2,6 s fixos sumiam com o aviso do sudo antes de a pessoa terminar de ler
  return Math.min(2000 + 50 * String(texto || "").length, 9000);
}

function piscarAviso(texto) {
  let caixa = el("aviso");
  if (!caixa) {
    caixa = document.createElement("div");
    caixa.id = "aviso";
    caixa.className = "aviso";
    document.body.appendChild(caixa);
    // com o mouse em cima ele espera; saindo, conta de novo
    caixa.addEventListener("mouseenter", () => clearTimeout(avisoRelogio));
    caixa.addEventListener("mouseleave", () => {
      clearTimeout(avisoRelogio);
      avisoRelogio = setTimeout(() => caixa.classList.remove("visivel"), 1500);
    });
  }
  caixa.textContent = texto;
  caixa.classList.add("visivel");
  clearTimeout(avisoRelogio);
  avisoRelogio = setTimeout(() => caixa.classList.remove("visivel"), tempoDoAviso(texto));
}

const elMenuAba = el("menu-aba");
let abaDoMenu = null;

function pastaDaAba(aba) {
  /* A pasta mais certa que a aba conhece: a da conversa, a que o shell anunciou e a de onde nasceu. */
  if (!aba || aba.ehPainel) return "";
  return (aba.sessao && aba.sessao.pasta) || aba.pastaAtual || (aba.pedidoInicial || {}).pasta || "";
}

function contaRemovida(aba) {
  // a aba segue com o perfil antigo; abrir outra nele caia calada na conta de sempre
  return !!aba.perfil && !perfis.some((p) => p.id === aba.perfil.id);
}

function abrirMenuAba(aba, x, y) {
  abaDoMenu = aba;
  const soEsta = elMenuAba.querySelector('[data-aba="so-esta"]');
  const grade = elMenuAba.querySelector('[data-aba="grade"]');
  soEsta.hidden = !emGrade && !divisao;
  grade.hidden = emGrade || ordem.filter((a) => !a.ehPainel).length < 2;
  // dividir leva a aba clicada pro lado da que esta em foco; a propria em foco nao tem com quem
  elMenuAba.querySelector('[data-aba="dividir"]').hidden =
    emGrade || aba.ehPainel || aba === abaAtiva || naDivisao(aba) || !abaAtiva || abaAtiva.ehPainel;
  elMenuAba.querySelector('[data-aba="desfazer-divisao"]').hidden = !divisao;
  const pasta = pastaDaAba(aba);
  const copiar = elMenuAba.querySelector('[data-aba="copiar"]');
  copiar.disabled = !pasta;
  const novaPasta = elMenuAba.querySelector('[data-aba="nova-pasta"]');
  novaPasta.hidden = aba.ehPainel;
  novaPasta.disabled = !pasta;
  novaPasta.title = pasta || "Esta aba ainda não sabe em que pasta está";
  // a conversa que rodava aqui antes de o app fechar: o abas.json guardava e ninguem lia
  const retomar = elMenuAba.querySelector('[data-aba="retomar-antes"]');
  retomar.hidden =
    aba.ehPainel || !aba.conversaAnterior || !!(aba.sessao && aba.sessao.sessao === aba.conversaAnterior);
  const temTermo = !aba.ehPainel && aba.term.hasSelection();
  elMenuAba.querySelector('[data-aba="copiar-selecao"]').hidden = !temTermo;
  elMenuAba.querySelector('[data-aba="colar"]').hidden = aba.ehPainel;
  elMenuAba.querySelector('[data-aba="renomear"]').hidden = aba.ehPainel;
  // grupo sem nenhum item a vista nao deixa a linha de separar sobrando
  for (const grupo of elMenuAba.querySelectorAll(".menu-grupo")) {
    grupo.hidden = ![...grupo.querySelectorAll("[data-aba]")].some((b) => !b.hidden);
  }
  elMenuAba.hidden = false;
  const caixa = elMenuAba.getBoundingClientRect();
  elMenuAba.style.left = Math.min(x, window.innerWidth - caixa.width - 10) + "px";
  elMenuAba.style.top = Math.min(y, window.innerHeight - caixa.height - 10) + "px";
}

elMenuAba.querySelectorAll("[data-aba]").forEach((b) => {
  b.addEventListener("click", () => {
    const aba = abaDoMenu;
    elMenuAba.hidden = true;
    abaDoMenu = null;
    if (!aba) return;
    const acao = b.dataset.aba;
    if (acao === "fechar") fecharComCuidado(aba);
    else if (acao === "copiar-selecao") {
      const texto = aba.term.getSelection();
      const linhas = contarLinhas(texto);
      copiarComAviso(texto, linhas + (linhas === 1 ? " linha copiada" : " linhas copiadas"));
      aba.term.clearSelection();
      aba.foco();
    } else if (acao === "colar") colarAqui(aba);
    else if (acao === "renomear") renomearAba(aba);
    else if (acao === "selecionar") selecionarTudo(aba);
    else if (acao === "copiar-tudo") copiarTudo(aba);
    else if (acao === "limpar") limparTela(aba);
    else if ((acao === "duplicar" || acao === "nova-pasta" || acao === "retomar-antes") && contaRemovida(aba)) {
      piscarAviso("A conta desta aba foi removida");
    } else if (acao === "duplicar") novaAba(aba.shell.id, aba.perfil ? aba.perfil.id : "");
    else if (acao === "nova-pasta") {
      // segundo Claude no mesmo repositorio: mesma conta, mesmo shell e ja na pasta
      novaAba(aba.shell.id, aba.perfil ? aba.perfil.id : "", { pasta: pastaDaAba(aba) });
    } else if (acao === "retomar-antes") retomarAntes(aba);
    else if (acao === "copiar") {
      const pasta = pastaDaAba(aba);
      copiarComAviso(pasta, "Caminho copiado: " + pasta);
    } else if (acao === "so-esta") {
      ativar(aba);
      alternarGrade(false);
      desfazerDivisao();
    } else if (acao === "dividir") dividirCom(aba);
    else if (acao === "desfazer-divisao") desfazerDivisao();
    else if (acao === "grade") {
      ativar(aba);
      alternarGrade(true);
    }
  });
});

document.addEventListener("mousedown", (ev) => {
  if (!elMenuAba.hidden && !elMenuAba.contains(ev.target)) {
    elMenuAba.hidden = true;
    abaDoMenu = null;
  }
});

function copiarComAviso(texto, aviso) {
  /* Copiar a selecao e o caminho fechavam o menu sem dizer nada, nem quando falhava. */
  if (!texto) return;
  let promessa = null;
  try {
    promessa = navigator.clipboard.writeText(texto);
  } catch (e) {
    promessa = null;
  }
  if (!promessa || typeof promessa.then !== "function") {
    piscarAviso("Não consegui copiar");
    return;
  }
  promessa.then(
    () => piscarAviso(aviso),
    () => piscarAviso("Não consegui copiar")
  );
}

function retomarAntes(aba) {
  /* A conversa que rodava na aba volta nela mesma, quando o shell esta parado no prompt.
     Abrir outra aba deixava duas por conversa: quem retomava cinco depois de reabrir o app
     ficava com dez, metade com um Claude em branco. Com programa rodando, abre outra. */
  const conversa = aba.conversaAnterior;
  if (!conversa) return;
  const perfilId = aba.perfil ? aba.perfil.id : "";
  const emOutra = () => {
    aba.conversaAnterior = "";
    anotarAbas();
    novaAba(aba.shell.id, perfilId, { pasta: pastaDaAba(aba), conversa });
  };
  if (!bridge || !aba.viva || !aba.sessionId) {
    emOutra();
    return;
  }
  bridge.retomarNaAba(aba.sessionId, perfilId, conversa, (retomou) => {
    if (!retomou) {
      emOutra();
      return;
    }
    aba.conversaAnterior = "";
    aba.tituloAnterior = "";
    aba.chaveRefletida = "";
    aba.pintar();
    anotarAbas();
    ativar(aba);
    setTimeout(atualizarSessoes, 1500);
  });
}

function renomearAba(aba) {
  /* Nome proprio pra aba, que ganha de tudo. Vazio devolve o nome automatico. */
  const topo = aba.botao.querySelector(".sessao-topo");
  const alvo = aba.botao.querySelector(".rotulo");
  if (!topo || !alvo || aba.renomeando) return;
  aba.renomeando = true;

  const campo = document.createElement("input");
  campo.className = "rotulo-campo";
  campo.value = aba.apelido || alvo.textContent || "";
  campo.spellcheck = false;
  campo.placeholder = "Nome da aba";
  alvo.hidden = true;
  topo.insertBefore(campo, alvo);
  campo.focus();
  campo.select();

  const encerrar = (salvar) => {
    if (!aba.renomeando) return;
    aba.renomeando = false;
    if (salvar) aba.apelido = campo.value.trim();
    campo.remove();
    alvo.hidden = false;
    aba.chaveRefletida = "";
    if (aba.sessao) aba.refletir(aba.sessao);
    else aba.pintar();
    // o topo do painel e o que diz na grade qual painel e qual
    if (emGrade || naDivisao(aba)) pintarChapeu(aba);
    anotarAbas();
    aba.foco();
  };

  campo.addEventListener("keydown", (ev) => {
    ev.stopPropagation();
    if (ev.key === "Enter") encerrar(true);
    else if (ev.key === "Escape") encerrar(false);
  });
  campo.addEventListener("blur", () => encerrar(true));
  campo.addEventListener("mousedown", (ev) => ev.stopPropagation());
}

function retomarSessao(s, mesmoTrabalhando) {
  /* Abre uma aba na conta certa e manda o CLI continuar aquela conversa. */
  const conta = contaDaSessao(s);
  const perfil = perfis.find((p) => p.id === conta);
  if (!mesmoTrabalhando && (s.estado === "pensando" || s.estado === "rodando")) {
    // o aviso de 2,6 s saia com a aba ja abrindo, e ficavam dois Claude na mesma conversa.
    // O estado vem do transcript e pode seguir working um tempo depois de o outro cair, entao
    // pergunta em vez de barrar
    perguntar(
      "Retomar uma conversa que está trabalhando",
      "Essa conversa ainda está trabalhando em outro terminal. Retomar aqui cria um segundo Claude nela.",
      [
        { rotulo: "Cancelar", foco: true },
        { rotulo: "Retomar mesmo assim", classe: "botao-perigo", acao: () => retomarSessao(s, true) },
      ]
    );
    return;
  }
  if (!perfil && s.cli === "codex") {
    // a aba sem conta usa o ~/.codex de sempre; o pedido diz que e o Codex quem retoma, senao
    // subia o Claude com um id que ele nao conhece
    novaAba("", "", { pasta: s.pasta, conversa: s.sessao, cli: "codex" });
    return;
  }
  if (!perfil && (conta === "__padrao__" || s.perfil === "__padrao__")) {
    // o transcript esta na pasta de login de sempre, que e a que a aba sem conta usa. Vale
    // tambem pra conversa da Principal adotada e depois removida, que continua nessa pasta
    novaAba("", "", { pasta: s.pasta, conversa: s.sessao });
    return;
  }
  if (!perfil) {
    // conta criada aqui e removida: recadastrar faz pasta nova, entao nao ha o que indicar
    piscarAviso("Essa sessão é de uma conta que não está mais cadastrada aqui");
    return;
  }
  novaAba("", perfil.id, { pasta: s.pasta, conversa: s.sessao });
}

let janelaAtiva = true;
const estadoAnterior = new Map();

function avisarProntas(terminais) {
  /* Sessao que termina e passa a esperar por voce com a janela atras vira notificacao do
     Windows. So a virada avisa: repetir a cada ciclo seria alarme, nao aviso. */
  const vistas = new Set();
  for (const aba of terminais) {
    const s = aba.sessao;
    if (!s) continue;
    vistas.add(s.sessao);
    const antes = estadoAnterior.get(s.sessao);
    estadoAnterior.set(s.sessao, s.estado);
    if (antes === undefined || antes === s.estado) continue;
    if (s.estado !== "aguardando") continue;
    if (antes !== "pensando" && antes !== "rodando") continue;
    if (janelaAtiva && abaAtiva === aba) continue;
    if (!cfg.avisarQuandoPronta) continue;
    // Com a janela na frente o balao do Windows caia por cima do que a pessoa fazia, e a
    // opcao promete o aviso so com a janela atras. Na frente, o cartao da aba acende um instante.
    if (janelaAtiva) {
      destacarCartao(aba);
      continue;
    }
    // o id vai junto: o clique no balao leva pra esta aba
    if (bridge) bridge.notificar(aba.nomeDoCartao(), textoDoAviso(s), aba.sessionId || "");
  }
  for (const id of [...estadoAnterior.keys()]) if (!vistas.has(id)) estadoAnterior.delete(id);
}

function textoDoAviso(s) {
  if (s.espera === "pergunta") return "Tem uma pergunta esperando você";
  if (s.espera === "permissao") return "Parou pra pedir sua permissão";
  return "Terminou e está esperando você";
}

function destacarCartao(aba) {
  const cartao = aba.botao;
  cartao.classList.remove("avisou");
  // tira e poe no quadro seguinte, pra animacao recomecar se ela ja estava acesa
  requestAnimationFrame(() => {
    cartao.classList.add("avisou");
    setTimeout(() => cartao.classList.remove("avisou"), 1800);
  });
}

window.addEventListener("focus", () => {
  janelaAtiva = true;
  atualizarSessoes();
  atualizarLimites();
});
window.addEventListener("blur", () => {
  janelaAtiva = false;
});

let varrendo = false;
let relogioSessoes = null;
// quando a varredura em andamento comecou. Resposta que nunca chega, de uma chamada que falhou do
// lado do app, deixava varrendo ligado pra sempre: a lista e o relogio paravam, e os estados dos
// cartoes so voltavam fechando e abrindo o Loom
let varrendoDesde = 0;
const VARREDURA_PRESA_MS = 15000;

let assinaturaSessoes = "";

let pedidoPendente = false;

// ids das abas com programa rodando no shell; null enquanto nao se sabe, e ai nada muda
let abasComPrograma = null;

function atualizarSessoes() {
  if (!bridge) return;
  if (varrendo && Date.now() - varrendoDesde < VARREDURA_PRESA_MS) {
    // aviso que chega no meio de uma varredura nao pode sumir, senao a mudanca de estado
    // so aparece no proximo relogio, ate 2,5 s depois
    pedidoPendente = true;
    return;
  }
  varrendo = true;
  varrendoDesde = Date.now();
  // rede de seguranca: o relogio normal so e refeito quando a lista chega, entao sem isto uma
  // resposta perdida parava tudo. Chegando, o receberSessoes troca este pelo ritmo de sempre
  clearTimeout(relogioSessoes);
  relogioSessoes = setTimeout(atualizarSessoes, VARREDURA_PRESA_MS + 500);
  bridge.abasComPrograma((ids) => {
    try {
      abasComPrograma = new Set(JSON.parse(ids));
    } catch (e) {
      abasComPrograma = null;
    }
    // a varredura dos transcript roda fora da thread da tela, e a lista chega por sessoesProntas
    bridge.pedirSessoes();
  });
}

function receberSessoes(json) {
  varrendo = false;
  try {
    sessoes = JSON.parse(json);
  } catch (e) {
    sessoes = [];
  }
  // A idade anda a cada segundo, entao comparar o JSON cru NUNCA casava e a lateral
  // inteira era refeita a cada ciclo: a animacao do thinking reiniciava e soluçava.
  // Fora a idade, o texto so muda quando algo de verdade mudou. Entra junto quais abas
  // estao com o terminal andando, porque isso muda o estado que o cartao mostra; so as de
  // conversa trabalhando, senao um shell que imprime aos soluços refazia a lateral inteira.
  // E quais tem programa rodando, que e o que diz se o Claude da aba ainda existe.
  const chave =
    json.replace(/"idade":\s*\d+/g, '"idade":0') +
    "|" +
    ordem
      .filter((a) => !a.ehPainel && a.sessao && TRABALHO.has(a.sessao.bruto) && teveSaidaRecente(a))
      .map((a) => a.sessionId)
      .join(",") +
    "|" +
    (abasComPrograma ? [...abasComPrograma].sort().join(",") : "?");
  try {
    if (chave !== assinaturaSessoes) {
      assinaturaSessoes = chave;
      montarSessoes();
    } else {
      envelhecer();
    }
  } catch (e) {
    // um cartao que quebra ao pintar nao pode levar junto o relogio da lista inteira
    console.error("a lista de sessoes nao pintou", e);
    assinaturaSessoes = "";
  }
  reagendarSessoes();
  if (pedidoPendente) {
    pedidoPendente = false;
    setTimeout(atualizarSessoes, 60);
  }
}

function envelhecer() {
  /* Nada mudou de verdade: so o relogio de cada cartao anda, sem refazer a lista. */
  const porId = new Map(sessoes.map((s) => [s.sessao, s]));
  const escrever = (raiz, s) => {
    const alvo = raiz.querySelector(".sessao-idade");
    const texto = tempoCurto(s.idade);
    if (alvo && alvo.textContent !== texto) alvo.textContent = texto;
  };
  for (const aba of ordem) {
    if (aba.ehPainel || !aba.sessao) continue;
    const s = porId.get(aba.sessao.sessao);
    if (!s) continue;
    aba.sessao = comoNaAba(s, aba);
    escrever(aba.botao, s);
  }
}

function reagendarSessoes() {
  /* O aviso de arquivo mexido e quem manda; este relogio e so a rede de seguranca
     pra idade envelhecer na tela e pra pegar o que o vigia deixar passar. */
  const trabalhando = (s) => !!s && (s.estado === "rodando" || s.estado === "pensando");
  const acesa = sessoes.some(trabalhando) || ordem.some((a) => !a.ehPainel && trabalhando(a.sessao));
  clearTimeout(relogioSessoes);
  const ritmo = !janelaAtiva ? 8000 : acesa ? 1200 : 2500;
  relogioSessoes = setTimeout(atualizarSessoes, ritmo);
}

/* ------------------------------------------------------------- colar */

function colarAqui(aba) {
  /* Ctrl+V aceita o que estiver na area de transferencia. Texto vai direto; imagem e
     arquivo viram caminho em disco, que e o que o CLI entende.

     Quem le e o lado do Qt, nao o navigator.clipboard: aquele exige documento em foco e
     permissao, e quando qualquer uma das duas falha o Ctrl+V nao colava nada. */
  if (!aba || aba.ehPainel || !aba.sessionId) return;
  if (!bridge) return;
  bridge.lerAreaDeTransferencia((json) => {
    let dados;
    try {
      dados = JSON.parse(json);
    } catch (e) {
      dados = null;
    }
    if (!dados || !dados.tipo) {
      colarPeloNavegador(aba);
      return;
    }
    if (dados.tipo === "texto") entregarColagem(aba, dados.valor);
    else if (dados.tipo === "imagem") colarCaminhos(aba, [dados.valor]);
    else if (dados.tipo === "arquivos") colarCaminhos(aba, dados.valor);
  });
}

function comAspas(caminho) {
  return /[\s"']/.test(caminho) ? '"' + caminho.replace(/"/g, '\\"') + '"' : caminho;
}

function caminhoLiteral(caminho, aba) {
  const texto = String(caminho);
  if (/[\r\n\0]/.test(texto)) return null;
  const shell = aba.shell && aba.shell.id;
  if (shell === "pwsh" || shell === "powershell") return "'" + texto.replace(/'/g, "''") + "'";
  if (["bash", "wsl", "zsh", "sh", "dash", "ksh"].includes(shell)) return "'" + texto.replace(/'/g, "'\"'\"'") + "'";
  // no fish a barra invertida conta dentro das aspas simples, e a aspa se escapa com ela
  if (shell === "fish") return "'" + texto.replace(/([\\'])/g, "\\$1") + "'";
  // cmd expande %VAR% mesmo entre aspas; !VAR! tambem com expansao atrasada.
  if (shell === "cmd" && !/["%!]/.test(texto)) return '"' + texto + '"';
  return null;
}

function colarCaminhos(aba, caminhos, fim = "") {
  const citados = caminhos.map((c) => caminhoLiteral(c, aba));
  if (citados.some((c) => c === null)) {
    piscarAviso("Esse caminho contém caracteres que o shell pode interpretar. Cole como texto no programa.");
    return false;
  }
  aba.term.paste(citados.join(" ") + fim);
  return true;
}

async function colarPeloNavegador(aba) {
  /* Rede de seguranca pra formato que o Qt entrega vazio, por exemplo imagem que so
     existe como text/html com data URI dentro. */
  try {
    const itens = await navigator.clipboard.read();
    for (const item of itens) {
      const tipoImagem = item.types.find((t) => t.startsWith("image/"));
      if (tipoImagem) {
        const blob = await item.getType(tipoImagem);
        const base64 = await paraBase64(blob);
        bridge.salvarColagem(base64, tipoImagem.split("/")[1] || "png", (caminho) => {
          if (caminho) colarCaminhos(aba, [caminho]);
          else piscarAviso("Não consegui guardar essa imagem");
        });
        return;
      }
      if (item.types.includes("text/plain")) {
        entregarColagem(aba, await (await item.getType("text/plain")).text());
        return;
      }
    }
  } catch (e) {
    piscarAviso("Não há nada na área de transferência");
  }
}

function abaNoPonto(ponto) {
  /* A aba cuja tela esta debaixo do ponto da pagina, se alguma. */
  if (!ponto || typeof document.elementFromPoint !== "function") return null;
  const alvo = document.elementFromPoint(ponto.x, ponto.y);
  const tela = alvo && typeof alvo.closest === "function" ? alvo.closest(".tela") : null;
  return (tela && ordem.find((a) => a.tela === tela)) || null;
}

function soltarArquivos(caminhos, ponto) {
  /* Arrastar arquivo pra dentro escreve o caminho, que e o que o terminal entende.
     Caminho com espaco vai entre aspas, senao o shell parte no meio. Na grade vai pro painel
     debaixo do mouse: ia pro ativo, e o print solto no painel de baixo entrava na conversa
     do de cima. */
  const noPonto = abaNoPonto(ponto);
  const aba = noPonto && !noPonto.ehPainel && noPonto.sessionId ? noPonto : abaAtiva;
  if (!aba || aba.ehPainel || !aba.sessionId) return;
  if (!caminhos.length) return;
  if (aba !== abaAtiva) ativar(aba);
  if (!colarCaminhos(aba, caminhos, " ")) return;
  aba.foco();
  piscarAviso(caminhos.length === 1 ? "Caminho colado" : caminhos.length + " caminhos colados");
}

function colarNaAba(aba, texto) {
  // a caixa pode ter ficado aberta enquanto a aba fechava ou o shell saia
  if (ordem.indexOf(aba) < 0 || !aba.viva) return;
  aba.term.paste(texto);
  aba.foco();
}

function entregarColagem(aba, texto) {
  /* O xterm so envolve a colagem nas marcas quando o programa pede, e o Claude pede; o cmd e o
     PowerShell nao. Colar um bloco de comandos copiado de um README rodava cada linha na hora,
     inclusive a que era so exemplo. Com varias linhas num shell desses, pergunta antes. */
  const bruto = String(texto);
  const semFim = bruto.replace(/(\r\n|\r|\n)+$/, "");
  const linhas = semFim ? contarLinhas(semFim) : 0;
  const protegida = !!(aba.term.modes && aba.term.modes.bracketedPasteMode);
  if (linhas < 2 || protegida) {
    aba.term.paste(bruto);
    return;
  }
  perguntar(
    "Colar " + linhas + " linhas",
    "Este shell roda cada linha colada na hora, uma depois da outra. Numa linha só, as quebras " +
      "viram espaço e nada roda até você apertar Enter.",
    [
      { rotulo: "Cancelar" },
      { rotulo: "Colar e rodar", classe: "botao-perigo", acao: () => colarNaAba(aba, bruto) },
      {
        rotulo: "Colar numa linha só",
        classe: "botao-forte",
        foco: true,
        acao: () => colarNaAba(aba, semFim.replace(/[ \t]*(\r\n|\r|\n)[ \t]*/g, " ")),
      },
    ]
  );
}

function paraBase64(blob) {
  return new Promise((resolve, reject) => {
    const leitor = new FileReader();
    leitor.onload = () => resolve(String(leitor.result).split(",")[1] || "");
    leitor.onerror = reject;
    leitor.readAsDataURL(blob);
  });
}

const QUEBRA_DE_LINHA = new RegExp('\\r\\n|\\r|\\n', 'g');

function contarLinhas(texto) {
  return texto.split(QUEBRA_DE_LINHA).length;
}

/* ------------------------------------------------------------- busca */

const elBusca = el("busca");
const elBuscaTermo = el("busca-termo");

function abrirBusca() {
  if (!abaAtiva || abaAtiva.ehPainel) return;
  elBusca.hidden = false;
  elBuscaTermo.select();
  elBuscaTermo.focus();
}

function fecharBusca() {
  elBusca.hidden = true;
  el("busca-conta").textContent = "";
  if (abaAtiva) {
    if (abaAtiva.busca) abaAtiva.busca.clearDecorations();
    abaAtiva.foco();
  }
}

const PINTURA = {
  decorations: {
    matchBackground: "#3a2f10",
    matchBorder: "#d5b06b",
    matchOverviewRuler: "#d5b06b",
    activeMatchBackground: "#5a4413",
    activeMatchBorder: "#f0cd85",
    activeMatchColorOverviewRuler: "#f0cd85",
  },
};

function buscar(paraTras, digitando) {
  if (!abaAtiva || abaAtiva.ehPainel || !abaAtiva.busca) return;
  const termo = elBuscaTermo.value;
  if (!termo) {
    abaAtiva.busca.clearDecorations();
    el("busca-conta").textContent = "";
    return;
  }
  // digitando a busca fica na ocorrencia atual em vez de pular a cada letra
  const opcoes = digitando ? Object.assign({ incremental: true }, PINTURA) : PINTURA;
  const achou = paraTras
    ? abaAtiva.busca.findPrevious(termo, opcoes)
    : abaAtiva.busca.findNext(termo, opcoes);
  // onDidChangeResults escreve a contagem; aqui so o caso de nao achar nada
  if (!achou) el("busca-conta").textContent = "nada";
}

elBuscaTermo.addEventListener("input", () => buscar(false, true));
elBuscaTermo.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") {
    ev.preventDefault();
    buscar(ev.shiftKey);
  } else if (ev.key === "Escape") {
    ev.preventDefault();
    fecharBusca();
  }
});
el("busca-antes").addEventListener("click", () => buscar(true));
el("busca-depois").addEventListener("click", () => buscar(false));
el("busca-fecha").addEventListener("click", fecharBusca);

/* ------------------------------------------------- confirmar ao fechar */

let fechamentoPendente = null;

function fecharComCuidado(aba) {
  if (!cfg.confirmarFechar || !aba.viva || aba.ehPainel) {
    fechar(aba);
    return;
  }
  /* O shell da aba esta sempre vivo, entao perguntar so por isso abria a caixa em todo
     PowerShell parado no prompt, e ensinava a confirmar sem ler. Pergunta quando ha conversa
     do Claude na aba ou algum programa rodando no shell; na duvida, pergunta. */
  const conversando = aba.sessao && ["pensando", "rodando", "aguardando"].includes(aba.sessao.estado);
  if (conversando || aba.elevado || !aba.sessionId || !bridge) {
    pedirConfirmacaoDeFechar(aba);
    return;
  }
  bridge.temFilhos(aba.sessionId, (ocupado) => {
    if (ordem.indexOf(aba) < 0) return;
    if (ocupado) pedirConfirmacaoDeFechar(aba);
    else fechar(aba);
  });
}

function pedirConfirmacaoDeFechar(aba) {
  fechamentoPendente = aba;
  el("fechar-nome").textContent = aba.sessao
    ? nomeDaTela(aba)
    : aba.shell.nome + (aba.perfil ? " · " + aba.perfil.nome : "");
  // a aba elevada sempre pergunta: o que roda nela fica fora do alcance da consulta de filhos
  el("fechar-texto").textContent = aba.sessao
    ? "Tem uma sessão do Claude rodando nessa aba. Fechar encerra ela junto."
    : aba.elevado
    ? "O shell de administrador dessa aba ainda está aberto. Fechar encerra ele e o que estiver rodando dentro."
    : "Tem um programa rodando no shell dessa aba. Fechar encerra ele junto.";
  abrirVeu("veu-fechar");
}

el("confirmar-fechamento").addEventListener("click", () => {
  const alvo = fechamentoPendente;
  fechamentoPendente = null;
  fecharVeu("veu-fechar");
  if (alvo) fechar(alvo);
});

/* --------------------------------------------------- guardar as abas */

let gravarAbas = null;
let ultimaAnotacao = "";

function anotarAbas() {
  // vai sempre pro app, que guarda em memoria a ordem e o apelido de cada shell pra tela
  // refeita; o disco so recebe quando restaurar abas esta ligado
  if (!bridge) return;
  clearTimeout(gravarAbas);
  gravarAbas = setTimeout(() => {
    const lista = ordem
      .filter((a) => !a.ehPainel && a.viva)
      .map((a) => ({
        // a reaberta que ainda espera o shell novo aponta pro historico guardado da de antes
        sessao: a.sessionId || a.sessaoAnterior || "",
        shell: a.shell.id,
        perfil: a.perfil ? a.perfil.id : "",
        // a pasta da conversa, a que o shell anunciou ou a de onde nasceu: so a de nascimento
        // fazia a aba de shell voltar pra pasta pessoal depois de um cd
        pasta: pastaDaAba(a),
        // a conversa de antes vai junto enquanto nao houver outra, senao reabrir duas vezes
        // seguidas perdia o caminho de volta pra ela
        conversa: a.sessao ? a.sessao.sessao || "" : a.conversaAnterior || "",
        titulo: a.sessao
          ? tituloVivoDaAba(a) || a.sessao.titulo || ""
          : a.conversaAnterior
          ? a.tituloAnterior || ""
          : "",
        apelido: a.apelido || "",
        ativa: a === abaAtiva,
        // o lado da tela dividida, pra tela refeita reencontrar a divisao
        lado: divisao && divisao.esq === a ? "esq" : divisao && divisao.dir === a ? "dir" : "",
      }));
    // sem comparar, abas.json era reescrito em disco a cada ciclo de sessoes, o dia inteiro
    const texto = JSON.stringify(lista);
    if (texto === ultimaAnotacao) return;
    ultimaAnotacao = texto;
    bridge.saveTabs(texto);
  }, 600);
}

/* O programa que rodava na aba deixou o terminal num estado que o shell novo nao espera: tela
   alternativa, mouse ligado, colagem marcada, foco avisado, cursor escondido, cor, margem de
   rolagem, setas no modo de aplicacao. Sem zerar, o pwsh recebia o clique como lixo no prompt. */
const ZERAR_DEPOIS_DO_RESTAURO =
  "\x1b[?1049l\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l\x1b[?1004l\x1b[?2004l" +
  "\x1b[?1l\x1b>\x1b[r\x1b[?25h\x1b[0m";

function quandoDoRestauro(em) {
  // em vem em segundos, da hora em que o historico foi gravado; sem ela, vale agora
  const d = new Date(em > 0 ? em * 1000 : Date.now());
  const dois = (n) => ("0" + n).slice(-2);
  return dois(d.getDate()) + "/" + dois(d.getMonth() + 1) + " " + dois(d.getHours()) + ":" + dois(d.getMinutes());
}

function linhasDaTela(term) {
  const buffer = term.buffer.active;
  const linhas = [];
  for (let i = 0; i < term.rows; i++) {
    const linha = buffer.getLine(buffer.baseY + i);
    linhas.push(linha ? linha.translateToString(true) : "");
  }
  return linhas;
}

function emendaDoRestauro(linhas, em) {
  /* Poe o cursor logo abaixo da ultima linha com texto da tela e escreve a linha de Restaurado.
     Onde o programa antigo deixou o cursor nao serve: o Claude para no meio da tela, e o prompt
     novo nascia por cima da conversa redesenhada. */
  let ultima = -1;
  for (let i = linhas.length - 1; i >= 0; i--) {
    if (String(linhas[i] || "").trim()) {
      ultima = i;
      break;
    }
  }
  let posicao;
  if (ultima < 0) posicao = "\x1b[1;1H";
  else if (ultima + 1 < linhas.length) posicao = "\x1b[" + (ultima + 2) + ";1H";
  // texto ate a ultima linha: desce rolando, senao a linha de Restaurado apagaria a ultima
  else posicao = "\x1b[" + linhas.length + ";1H\r\n";
  return posicao + "\x1b[38;5;240m── Restaurado · " + quandoDoRestauro(em) + " ──\x1b[0m\r\n";
}

function pedidoDaAbaSalva(s, retomar) {
  /* Volta a aba, a conta e a pasta. Com retomarConversas ligado, a que rodava uma conversa ja
     volta dentro dela, pelo comando na subida do shell, que nao depende do tempo de subida do
     CLI. Desligado, a aba volta parada no prompt e o Retomar fica no botao direito: com o CLI
     aberto sozinho nascia um Claude em branco em cada uma. */
  const pedido = { pasta: s.pasta || "" };
  if (s.conversa && retomar) pedido.conversa = s.conversa;
  else pedido.semCli = !!s.conversa;
  if (s.historico) {
    pedido.restauro = { texto: s.historico, cols: s.cols || 0, rows: s.rows || 0, em: s.historicoEm || 0 };
    pedido.anterior = s.sessao || "";
  }
  return pedido;
}

function restaurarAbas(depois) {
  bridge.savedTabs((json) => {
    let salvas = [];
    try {
      salvas = JSON.parse(json);
    } catch (e) {
      salvas = [];
    }
    if (!salvas.length) {
      depois(false);
      return;
    }
    let ativa = null;
    for (const s of salvas) {
      const aba = novaAba(s.shell, s.perfil, pedidoDaAbaSalva(s, !!cfg.retomarConversas));
      if (aba && s.conversa) {
        aba.conversaAnterior = s.conversa;
        aba.tituloAnterior = s.titulo || "";
      }
      if (aba && (s.apelido || s.conversa)) {
        aba.apelido = s.apelido || "";
        aba.chaveRefletida = "";
        aba.pintar();
      }
      if (aba && s.ativa) ativa = aba;
    }
    if (ativa) ativar(ativa);
    depois(true);
  });
}

/* ------------------------------------------------------------- grade */

let emGrade = false;

function nomeDaTela(aba) {
  if (aba.ehPainel) return "Uso";
  return aba.nomeDoCartao();
}

function medida(rotulo, valor, classe, dica) {
  const titulo = dica ? ' title="' + dica + '"' : "";
  return '<b class="' + (classe || "") + '"' + titulo + "><i>" + rotulo + "</i>" + valor + "</b>";
}

function faixaDaConta(aba, qual) {
  const dona = aba.perfil || contaPadraoCadastrada();
  const chave = dona ? dona.id : "__padrao__";
  if (dona && !dona.logado) return null;
  return ((limitesPorPerfil[chave] || {}).faixas || {})[qual] || null;
}

function grau(pct) {
  if (pct === null) return "";
  return pct >= 85 ? "critico" : pct >= 60 ? "atencao" : "";
}

function pintarChapeu(aba) {
  const dona = aba.perfil || contaPadraoCadastrada();
  const cor = dona ? dona.cor : "#5b6371";
  aba.tela.style.setProperty("--cor-conta", cor);
  aba.chapeu.querySelector(".chapeu-ponto").style.background = cor;

  aba.chapeu.querySelector(".chapeu-conta").textContent = dona ? dona.nome : "Principal";
  const nome = aba.chapeu.querySelector(".chapeu-nome");
  nome.textContent = nomeDaTela(aba);
  nome.title = aba.sessao ? aba.sessao.pasta || "" : "";

  const estado = aba.chapeu.querySelector(".chapeu-estado");
  const s = aba.sessao;
  estado.className = "chapeu-estado" + (s ? " " + s.estado : "");
  estado.textContent = s ? textoDoEstado(s) : "";
  estado.title = s ? dicaDoEstado(s) : "";

  /* So o que muda de painel pra painel: o contexto da conversa, que diz qual Claude esta perto
     de compactar. O 5h e o 7d sao da conta, repetiam o mesmo numero em todo painel dela, e
     ficam na barra de baixo, que segue o painel ativo. */
  const partes = [];
  const contexto = contextoDe(aba);
  if (contexto !== null) {
    const pct = Math.round(contexto);
    partes.push(medida("ctx", pct + "%", grau(pct), pct + "% da janela de contexto desta conversa"));
  }
  aba.chapeu.querySelector(".chapeu-medidas").innerHTML = partes.join("");
}

function contextoDe(aba) {
  const viva = leituraDaAba(aba);
  if (viva && typeof viva.contexto === "number") return viva.contexto;
  return aba.sessao && typeof aba.sessao.contexto === "number" ? aba.sessao.contexto : null;
}

// o painel mais estreito que ainda deixa o Claude escrever sem partir cada linha no meio
const LARGURA_MINIMA_DO_PAINEL = 480;

function colunasDaGrade(quantas, largura) {
  const pelaQuantidade = quantas <= 1 ? 1 : quantas <= 4 ? 2 : quantas <= 9 ? 3 : 4;
  // com cinco abas em meia tela eram tres colunas de uns 200px, 25 colunas de terminal
  const pelaLargura = largura > 0 ? Math.max(1, Math.floor(largura / LARGURA_MINIMA_DO_PAINEL)) : pelaQuantidade;
  return Math.min(pelaQuantidade, pelaLargura);
}

function pintarGrade() {
  const terminais = ordem.filter((a) => !a.ehPainel);
  const colunas = colunasDaGrade(terminais.length, elPalco.clientWidth);
  elPalco.style.setProperty("--colunas", colunas);
  for (const aba of terminais) pintarChapeu(aba);
}

function reencaixarGrade() {
  // a largura mudou: as colunas sao refeitas pela largura antes de cada terminal se remedir
  pintarGrade();
  ajustarTodas();
}

function ajustarTodas() {
  // cada terminal precisa remedir o proprio tamanho depois que o layout muda
  requestAnimationFrame(() => {
    for (const aba of ordem) if (!aba.ehPainel) aba.ajustar();
  });
}

function alternarGrade(ligar) {
  emGrade = typeof ligar === "boolean" ? ligar : !emGrade;
  // grade e divisao se excluem: a grade ja mostra todos, os dois lados inclusive
  if (emGrade && divisao) desfazerDivisao();
  elPalco.classList.toggle("grade", emGrade);
  sincronizarWebgl();
  document.querySelectorAll("#seletor-layout button").forEach((b) => {
    b.classList.toggle("ativo", (b.dataset.layout === "grade") === emGrade);
  });
  // a barra de baixo fica na grade e segue o painel ativo; aqui so a altura dos terminais muda
  pintarUsoDaBarra();
  if (emGrade) {
    if (abaAtiva && abaAtiva.ehPainel) {
      const primeiro = ordem.find((a) => !a.ehPainel);
      if (primeiro) ativar(primeiro);
    }
    pintarGrade();
  }
  ajustarTodas();
  if (abaAtiva) abaAtiva.foco();
}

document.querySelectorAll("#seletor-layout button").forEach((b) => {
  b.addEventListener("click", () => alternarGrade(b.dataset.layout === "grade"));
});

/* ------------------------------------------------------------- divisao */

/* Dois terminais lado a lado sem ir pra grade inteira: o da esquerda, o da direita e a divisoria
   arrastavel no meio. O palco vira uma grade de tres colunas so com os dois lados; as outras abas
   continuam escondidas por cima, como sempre. */

const elDivisoria = el("divisoria");
let arrastandoDivisoria = false;
let relogioDosLados = null;
let ladosObservados = "";

function naDivisao(aba) {
  return !!divisao && !!aba && (divisao.esq === aba || divisao.dir === aba);
}

function outroLado(aba) {
  if (!naDivisao(aba)) return null;
  return divisao.esq === aba ? divisao.dir : divisao.esq;
}

function fracaoDaDivisao() {
  const valor = Number(cfg.fracaoDivisao);
  return Math.min(Math.max(Number.isFinite(valor) ? valor : 0.5, 0.2), 0.8);
}

function colunasDaDivisao(fracao) {
  // em fr e nao em porcentagem: o vao da divisoria sai do total, e os dois lados nunca vazam
  return "minmax(0, " + fracao + "fr) 8px minmax(0, " + (1 - fracao) + "fr)";
}

function aplicarFracao() {
  elPalco.style.gridTemplateColumns = divisao ? colunasDaDivisao(fracaoDaDivisao()) : "";
}

const observadorDosLados =
  typeof ResizeObserver === "function"
    ? new ResizeObserver(() => {
        // cada lado remede quando o tamanho dele assenta: a lateral, o painel de uso e a janela
        // mudam a largura sem passar por aqui. Arrastando a divisoria, espera a mao parar, senao o
        // CLI redesenharia a tela inteira a cada pixel
        clearTimeout(relogioDosLados);
        relogioDosLados = setTimeout(ajustarLados, arrastandoDivisoria ? 300 : 90);
      })
    : null;

function observarLados() {
  const chave = divisao ? ordem.indexOf(divisao.esq) + ":" + ordem.indexOf(divisao.dir) : "";
  if (!observadorDosLados || chave === ladosObservados) return;
  ladosObservados = chave;
  observadorDosLados.disconnect();
  if (!divisao) return;
  observadorDosLados.observe(divisao.esq.tela);
  observadorDosLados.observe(divisao.dir.tela);
}

function ajustarLados() {
  clearTimeout(relogioDosLados);
  relogioDosLados = null;
  if (!divisao) return;
  requestAnimationFrame(() => {
    if (!divisao) return;
    divisao.esq.ajustar();
    divisao.dir.ajustar();
  });
}

function pintarDivisao() {
  for (const aba of ordem) {
    if (aba.ehPainel) continue;
    aba.tela.classList.toggle("lado", naDivisao(aba));
    aba.tela.classList.toggle("lado-esq", !!divisao && divisao.esq === aba);
    aba.tela.classList.toggle("lado-dir", !!divisao && divisao.dir === aba);
    // o cartao do outro lado tambem se marca na lateral, mais fraco que o da aba em foco
    aba.botao.classList.toggle("na-divisao", naDivisao(aba));
  }
  elPalco.classList.toggle("dividido", !!divisao);
  elDivisoria.hidden = !divisao;
  aplicarFracao();
  if (divisao) {
    pintarChapeu(divisao.esq);
    pintarChapeu(divisao.dir);
  }
  observarLados();
}

function dividir(esq, dir, foco) {
  /* Poe esq e dir lado a lado e da o foco a um deles. Sai da grade, que ja mostrava todos. */
  if (!esq || !dir || esq === dir || esq.ehPainel || dir.ehPainel) return false;
  if (ordem.indexOf(esq) < 0 || ordem.indexOf(dir) < 0) return false;
  if (emGrade) alternarGrade(false);
  divisao = { esq, dir };
  pintarDivisao();
  const alvo = foco === dir ? dir : esq;
  if (abaAtiva !== alvo) ativar(alvo);
  else sincronizarWebgl();
  ajustarLados();
  anotarAbas();
  return true;
}

function dividirCom(aba) {
  // a aba em foco fica a esquerda, onde voce estava digitando, e a escolhida vai pra direita
  if (!abaAtiva || abaAtiva.ehPainel || !aba || aba === abaAtiva) return false;
  return dividir(abaAtiva, aba, abaAtiva);
}

function trocarNaDivisao(aba) {
  /* A aba escolhida pela lateral, pelo atalho ou pela paleta entra no lado que estava em foco. */
  if (naDivisao(aba)) return;
  if (aba.ehPainel || !naDivisao(abaAtiva)) {
    desfazerDivisao();
    return;
  }
  if (divisao.esq === abaAtiva) divisao.esq = aba;
  else divisao.dir = aba;
  pintarDivisao();
  anotarAbas();
}

function desfazerDivisao() {
  if (!divisao) return;
  divisao = null;
  pintarDivisao();
  sincronizarWebgl();
  // o lado que ficou volta pra largura inteira
  requestAnimationFrame(() => {
    if (!abaAtiva) return;
    abaAtiva.ajustar();
    if (!veuAberto()) abaAtiva.foco();
  });
  anotarAbas();
}

function parceiraDaDivisao() {
  /* Com quem o atalho divide: a aba de antes, e senao a seguinte na lista. */
  const terminais = ordem.filter((a) => !a.ehPainel && a !== abaAtiva);
  if (abaAnterior && terminais.indexOf(abaAnterior) >= 0) return abaAnterior;
  const i = ordem.indexOf(abaAtiva);
  return ordem.slice(i + 1).concat(ordem.slice(0, i)).find((a) => terminais.indexOf(a) >= 0) || null;
}

function alternarDivisao() {
  /* Ctrl+Shift+D: divide com a aba de antes, e com uma aba so abre outra igual a ela do lado.
     Com a tela ja dividida, desfaz. */
  if (divisao) {
    desfazerDivisao();
    return;
  }
  if (!abaAtiva || abaAtiva.ehPainel) {
    piscarAviso("Abra uma aba de terminal pra dividir a tela");
    return;
  }
  const parceira = parceiraDaDivisao();
  if (parceira) {
    dividirCom(parceira);
    return;
  }
  const esquerda = abaAtiva;
  const perfil = esquerda.perfil && !contaRemovida(esquerda) ? esquerda.perfil.id : "";
  const pasta = pastaDaAba(esquerda);
  const nova = novaAba(esquerda.shell.id, perfil, pasta ? { pasta } : undefined);
  if (nova) dividir(esquerda, nova, nova);
}

(function ligarDivisoria() {
  let gravar = null;

  const guardar = () => {
    clearTimeout(gravar);
    gravar = setTimeout(() => {
      if (bridge) bridge.saveConfig(JSON.stringify({ fracaoDivisao: cfg.fracaoDivisao }), () => {});
    }, 400);
  };

  elDivisoria.addEventListener("mousedown", (ev) => {
    if (ev.button !== 0) return;
    ev.preventDefault();
    arrastandoDivisoria = true;
    elDivisoria.classList.add("ativo");
    document.body.classList.add("puxando-divisao");
  });

  window.addEventListener("mousemove", (ev) => {
    if (!arrastandoDivisoria || !divisao) return;
    // a conta desconta o respiro do palco e o vao da divisoria, que nao sao de nenhum dos lados
    const caixa = elPalco.getBoundingClientRect();
    const util = caixa.width - 16 - 8;
    if (util <= 0) return;
    const fracao = (ev.clientX - caixa.left - 8 - 4) / util;
    cfg.fracaoDivisao = Math.round(Math.min(Math.max(fracao, 0.2), 0.8) * 1000) / 1000;
    aplicarFracao();
    guardar();
  });

  window.addEventListener("mouseup", () => {
    if (!arrastandoDivisoria) return;
    arrastandoDivisoria = false;
    elDivisoria.classList.remove("ativo");
    document.body.classList.remove("puxando-divisao");
    ajustarLados();
    if (abaAtiva && !veuAberto()) abaAtiva.foco();
  });

  // duplo clique volta ao meio, como no VS Code
  elDivisoria.addEventListener("dblclick", () => {
    cfg.fracaoDivisao = 0.5;
    aplicarFracao();
    guardar();
  });
})();

/* --------------------------------------------- largura da lateral */

function aplicarLarguraLateral() {
  const valor = Math.min(Math.max(cfg.larguraLateral || 246, 180), 520);
  elLateral.style.setProperty("--largura-lateral", valor + "px");
}

(function ligarPuxador() {
  const puxador = el("puxador");
  let ativo = false;
  let gravar = null;

  puxador.addEventListener("mousedown", (ev) => {
    ev.preventDefault();
    ativo = true;
    puxador.classList.add("ativo");
    document.body.classList.add("puxando-largura");
  });

  window.addEventListener("mousemove", (ev) => {
    if (!ativo) return;
    const largura = Math.min(Math.max(ev.clientX, 180), 520);
    cfg.larguraLateral = Math.round(largura);
    aplicarLarguraLateral();
    clearTimeout(gravar);
    gravar = setTimeout(() => {
      bridge.saveConfig(JSON.stringify({ larguraLateral: cfg.larguraLateral }), () => {});
      if (emGrade) reencaixarGrade();
      else if (abaAtiva) abaAtiva.ajustar();
    }, 300);
  });

  window.addEventListener("mouseup", () => {
    if (!ativo) return;
    ativo = false;
    puxador.classList.remove("ativo");
    document.body.classList.remove("puxando-largura");
    if (emGrade) reencaixarGrade();
    else if (abaAtiva) abaAtiva.ajustar();
  });
})();

function alternarLateral() {
  elLateral.classList.toggle("fechada");
  setTimeout(() => {
    if (emGrade) reencaixarGrade();
    else if (abaAtiva) abaAtiva.ajustar();
  }, 200);
  if (!elLateral.classList.contains("fechada")) atualizarSessoes();
}

el("alternar-lateral").addEventListener("click", alternarLateral);

/* ------------------------------------------------------ barra da janela */

function ligarJanelaPropria(propria) {
  /* Sem a barra do sistema, o topo do Loom e a barra: arrasta, duplo clique maximiza, e as
     bordas mudam o tamanho. O arraste so comeca quando o mouse anda: o compositor toma o ponteiro
     dali em diante, e comecando no mousedown o duplo clique nunca chegaria aqui. */
  const linha = el("linha-barra-sistema");
  if (linha) linha.hidden = !propria && !cfg.barraDoSistema;
  if (!propria) return;
  document.body.classList.add("janela-propria");
  el("janela-botoes").hidden = false;
  el("bordas-janela").hidden = false;
  document.querySelectorAll("[data-janela]").forEach((botao) => {
    botao.addEventListener("click", () => bridge.pedirJanela(botao.dataset.janela));
  });
  document.querySelectorAll("[data-borda]").forEach((borda) => {
    borda.addEventListener("mousedown", (ev) => {
      if (ev.button !== 0) return;
      ev.preventDefault();
      bridge.pedirJanela("borda:" + borda.dataset.borda);
    });
  });
  const cromo = document.querySelector(".cromo");
  const ehParteLivre = (alvo) => !alvo.closest("button, input, select");
  let pressao = null;
  cromo.addEventListener("mousedown", (ev) => {
    pressao = ev.button === 0 && ehParteLivre(ev.target) ? { x: ev.clientX, y: ev.clientY } : null;
  });
  window.addEventListener("mousemove", (ev) => {
    if (!pressao) return;
    if (!ev.buttons) pressao = null;
    else if (Math.abs(ev.clientX - pressao.x) + Math.abs(ev.clientY - pressao.y) > 3) {
      pressao = null;
      bridge.pedirJanela("mover");
    }
  });
  window.addEventListener("mouseup", () => {
    pressao = null;
  });
  cromo.addEventListener("dblclick", (ev) => {
    if (ehParteLivre(ev.target)) bridge.pedirJanela("maximizar");
  });
  bridge.janelaMudou.connect((maximizada) => {
    document.body.classList.toggle("maximizada", maximizada);
    const botao = document.querySelector('[data-janela="maximizar"]');
    if (botao) botao.title = maximizada ? "Restaurar" : "Maximizar";
  });
}


/* ------------------------------------------------- monitor de recursos */

const RECURSOS_A_CADA_MS = 2000;
// as faixas de cada temperatura: a partir de onde chama atencao, e de onde o componente ja corta
const CALOR = { cpu: [85, 95], gpu: [85, 100], vram: [95, 105] };
// a ultima leitura veio com alguma coisa: fora do Linux nao vem nada, e a barra nem aparece
let recursosComLeitura = false;

function nivelDoUso(p) {
  return typeof p !== "number" ? "" : p >= 95 ? "critico" : p >= 85 ? "alto" : "";
}

function nivelDoCalor(t, faixas) {
  return typeof t !== "number" ? "" : t >= faixas[1] ? "critico" : t >= faixas[0] ? "alto" : "";
}

function textoDePorcento(p) {
  return typeof p === "number" ? Math.round(p) + "%" : "--";
}

function textoDeGraus(t) {
  return typeof t === "number" ? Math.round(t) + "°" : "";
}

function gigas(bytes) {
  return (bytes / 1073741824).toFixed(1).replace(".", ",");
}

function textoDeMemoria(m) {
  return gigas(m.usado) + "/" + Math.round(m.total / 1073741824) + " GB";
}

function itensDosRecursos(r) {
  /* O que cada item da barra escreve, separado de quem pinta. Item sem leitura nao vem. */
  const itens = {};
  if (r.cpu) {
    itens.cpu = {
      valor: textoDePorcento(r.cpu.uso), nivel: nivelDoUso(r.cpu.uso), parte: r.cpu.uso || 0,
      temp: textoDeGraus(r.cpu.temp), calor: nivelDoCalor(r.cpu.temp, CALOR.cpu),
      dica: "Processador: " + textoDePorcento(r.cpu.uso) + " em uso" + (typeof r.cpu.temp === "number" ? ", " + Math.round(r.cpu.temp) + " °C" : ""),
    };
  }
  if (r.ram && r.ram.total) {
    const parte = (100 * r.ram.usado) / r.ram.total;
    itens.ram = {
      valor: textoDeMemoria(r.ram), nivel: nivelDoUso(parte), parte, temp: "", calor: "",
      dica: "Memória: " + gigas(r.ram.usado) + " de " + gigas(r.ram.total) + " GB em uso, " + Math.round(parte) + "%",
    };
  }
  if (r.gpu) {
    const graus = [];
    if (typeof r.gpu.temp === "number") graus.push("borda " + Math.round(r.gpu.temp) + " °C");
    if (typeof r.gpu.juncao === "number") graus.push("junção " + Math.round(r.gpu.juncao) + " °C");
    itens.gpu = {
      valor: r.gpu.dormindo ? "off" : textoDePorcento(r.gpu.uso), nivel: nivelDoUso(r.gpu.uso), parte: r.gpu.uso || 0,
      temp: textoDeGraus(r.gpu.temp), calor: nivelDoCalor(Math.max(r.gpu.temp || 0, r.gpu.juncao || 0), CALOR.gpu),
      dica: r.gpu.dormindo
        ? "Placa de vídeo dormindo"
        : "Placa de vídeo: " + textoDePorcento(r.gpu.uso) + " em uso" + (graus.length ? ", " + graus.join(", ") : ""),
    };
  }
  if (r.vram && r.vram.total) {
    const parte = (100 * r.vram.usado) / r.vram.total;
    itens.vram = {
      valor: textoDeMemoria(r.vram), nivel: nivelDoUso(parte), parte,
      temp: textoDeGraus(r.vram.temp), calor: nivelDoCalor(r.vram.temp, CALOR.vram),
      dica: "Memória de vídeo: " + gigas(r.vram.usado) + " de " + gigas(r.vram.total) + " GB em uso" + (typeof r.vram.temp === "number" ? ", " + Math.round(r.vram.temp) + " °C" : ""),
    };
  }
  return itens;
}

function memoriaCurta(bytes) {
  if (typeof bytes !== "number") return "--";
  return bytes < 1073741824 ? Math.round(bytes / 1048576) + " MB" : gigas(bytes) + " GB";
}

function textoDeNucleos(n) {
  if (typeof n !== "number") return "--";
  const texto = n.toFixed(n < 10 ? 1 : 0).replace(".", ",");
  return texto + (texto === "1,0" ? " núcleo" : " núcleos");
}

function itensDaAba(r) {
  /* O que a linha de uma aba escreve: o programa que mais gasta nela e o total da aba inteira.
     A CPU e a parte da maquina, como na barra de cima; os nucleos ficam na dica. Placa so aparece
     quando algum programa da aba abriu a placa. */
  if (!r) return null;
  const principais = r.principais || [];
  const comPlaca = typeof r.vram === "number" && r.vram > 0;
  const linhas = principais.map((p) => {
    const partes = [textoDeNucleos(p.nucleos), memoriaCurta(p.ram)];
    if (typeof p.gpu === "number") partes.push("placa " + textoDePorcento(p.gpu));
    if (p.vram) partes.push(memoriaCurta(p.vram) + " de vídeo");
    return p.nome + ": " + partes.join(", ");
  });
  const processos = r.processos || principais.length;
  const dica =
    "Nesta aba agora, " + processos + (processos === 1 ? " processo" : " processos") +
    (typeof r.nucleos === "number" ? ", " + textoDeNucleos(r.nucleos) + " ocupados" : "") + ":\n" +
    linhas.join("\n") + (processos > linhas.length ? "\n..." : "");
  return {
    nome: principais.length ? principais[0].nome : "",
    cpu: { valor: textoDePorcento(r.cpu), nivel: nivelDoUso(r.cpu) },
    ram: { valor: memoriaCurta(r.ram), nivel: "" },
    gpu: comPlaca ? { valor: textoDePorcento(r.gpu), nivel: nivelDoUso(r.gpu) } : null,
    vram: comPlaca ? { valor: memoriaCurta(r.vram), nivel: "" } : null,
    quieta: !(r.cpu >= 1) && !(r.gpu >= 1),
    dica,
  };
}

function montarUsoDaAba(classe) {
  const caixa = document.createElement("div");
  caixa.className = classe + " uso-aba";
  caixa.hidden = true;
  caixa.innerHTML =
    '<span class="uso-aba-nome"></span>' +
    '<span class="uso-aba-item" data-item="cpu"><i>CPU</i><b></b></span>' +
    '<span class="uso-aba-item" data-item="ram"><i>RAM</i><b></b></span>' +
    '<span class="uso-aba-item" data-item="gpu"><i>GPU</i><b></b></span>' +
    '<span class="uso-aba-item" data-item="vram"><i>VRAM</i><b></b></span>';
  return caixa;
}

function pintarUsoDaAba(caixa, itens) {
  // muda so o que mudou, como a barra de cima: a cada 2 s, em cada aba
  const mostrar = !!itens && cfg.mostrarRecursos !== false;
  if (caixa.hidden !== !mostrar) caixa.hidden = !mostrar;
  if (!mostrar) return;
  escreverSeMudou(caixa.querySelector(".uso-aba-nome"), itens.nome);
  for (const nome of ["cpu", "ram", "gpu", "vram"]) {
    const item = caixa.querySelector('[data-item="' + nome + '"]');
    const dado = itens[nome];
    if (item.hidden !== !dado) item.hidden = !dado;
    if (!dado) continue;
    const valor = item.querySelector("b");
    escreverSeMudou(valor, dado.valor);
    if (valor.className !== dado.nivel) valor.className = dado.nivel;
  }
  if (caixa.classList.contains("quieta") !== itens.quieta) caixa.classList.toggle("quieta", itens.quieta);
  if (caixa.title !== itens.dica) caixa.title = itens.dica;
}

function itensDoMonitorDaAba(r, totais) {
  /* Os mesmos quatro itens da barra de cima, medindo so a aba. As barrinhas de RAM e VRAM dizem
     quanto da maquina a aba ocupa. Sem placa aberta, a placa fica em zero, como a barra de cima
     mostraria uma placa parada; antes da segunda leitura nao ha uso, e vai traco. */
  r = r || {};
  totais = totais || {};
  const primeira = typeof r.cpu !== "number";
  const fracao = (parte, total) => (typeof parte === "number" && total ? (100 * parte) / total : 0);
  const ram = fracao(r.ram, totais.ram);
  const vram = fracao(r.vram || 0, totais.vram);
  const gpu = primeira ? null : r.gpu || 0;
  const principal = (r.principais || [])[0];
  const doCartao = r.principais ? itensDaAba(r) : null;
  return {
    nome: principal ? principal.nome : "",
    cpu: { valor: textoDePorcento(r.cpu), parte: r.cpu || 0, nivel: nivelDoUso(r.cpu) },
    ram: { valor: typeof r.ram === "number" ? memoriaCurta(r.ram) : "--", parte: ram, nivel: nivelDoUso(ram) },
    gpu: { valor: textoDePorcento(gpu), parte: gpu || 0, nivel: nivelDoUso(gpu) },
    vram: { valor: typeof r.ram === "number" ? memoriaCurta(r.vram || 0) : "--", parte: vram, nivel: nivelDoUso(vram) },
    dica: doCartao ? doCartao.dica : "",
  };
}

function montarMonitorDaAba(classe) {
  const caixa = document.createElement("div");
  caixa.className = classe + " monitor-aba";
  caixa.hidden = true;
  caixa.innerHTML =
    '<span class="monitor-aba-nome"></span><span class="monitor-aba-itens">' +
    ["cpu", "ram", "gpu", "vram"]
      .map((nome) =>
        '<span class="recurso" data-item="' + nome + '"><i>' + nome.toUpperCase() + "</i>" +
        '<span class="recurso-trilho"><u></u></span><b></b></span>')
      .join("") +
    "</span>";
  return caixa;
}

function pintarMonitorDaAba(caixa, itens) {
  // como a barra de cima: so mexe no que mudou, e a largura da barrinha em numero inteiro
  const mostrar = !!itens;
  if (caixa.hidden !== !mostrar) caixa.hidden = !mostrar;
  if (!mostrar) return;
  escreverSeMudou(caixa.querySelector(".monitor-aba-nome"), itens.nome);
  for (const nome of ["cpu", "ram", "gpu", "vram"]) {
    const item = caixa.querySelector('[data-item="' + nome + '"]');
    const dado = itens[nome];
    const valor = item.querySelector("b");
    escreverSeMudou(valor, dado.valor);
    if (valor.className !== dado.nivel) valor.className = dado.nivel;
    const barra = item.querySelector("u");
    const largura = Math.round(Math.min(Math.max(dado.parte, 0), 100)) + "%";
    if (barra.style.width !== largura) barra.style.width = largura;
    if (barra.className !== dado.nivel) barra.className = dado.nivel;
  }
  if (caixa.title !== itens.dica) caixa.title = itens.dica;
}

function pintarUsoDasAbas(abas, totais = {}) {
  // a faixa do terminal existe enquanto o monitor tem leitura: aparece com traco antes do primeiro
  // numero da aba, pra o terminal nao encolher uma linha 2 s depois de abrir
  const faixa = cfg.mostrarRecursos !== false && recursosComLeitura;
  let mudou = false;
  for (const aba of ordem) {
    if (aba.ehPainel || !aba.usoNoCartao) continue;
    const dado = aba.viva && aba.sessionId ? abas[aba.sessionId] : null;
    pintarUsoDaAba(aba.usoNoCartao, itensDaAba(dado));
    const comFaixa = faixa && !!aba.viva;
    const itens = comFaixa ? itensDoMonitorDaAba(dado, totais) : null;
    pintarMonitorDaAba(aba.usoNoChapeu, dado ? itens : null);
    pintarMonitorDaAba(aba.usoNaTela, itens);
    if (aba.tela.classList.contains("com-uso") !== comFaixa) {
      aba.tela.classList.toggle("com-uso", comFaixa);
      mudou = true;
    }
  }
  // a faixa tira altura do terminal: cada um remede, e o shell recebe o tamanho novo
  if (mudou) ajustarTodas();
}

function escreverSeMudou(alvo, texto) {
  // o monitor anda a cada 2 s: sem isto, cada volta repintava a barra mesmo com o numero igual
  if (alvo && alvo.textContent !== texto) alvo.textContent = texto;
}

function pintarRecursos(json) {
  let dados = {};
  try {
    dados = JSON.parse(json) || {};
  } catch (e) {
    dados = {};
  }
  const itens = itensDosRecursos(dados);
  recursosComLeitura = Object.keys(itens).length > 0;
  for (const nome of ["cpu", "ram", "gpu", "vram"]) {
    const caixa = el("rec-" + nome);
    const item = itens[nome];
    if (caixa.hidden !== !item) caixa.hidden = !item;
    if (!item) continue;
    const valor = caixa.querySelector("b");
    escreverSeMudou(valor, item.valor);
    if (valor.className !== item.nivel) valor.className = item.nivel;
    const barra = caixa.querySelector("u");
    // em numero inteiro: uma casa decimal a mais seria um repinte a mais a cada leitura
    const largura = Math.round(Math.min(Math.max(item.parte, 0), 100)) + "%";
    if (barra.style.width !== largura) barra.style.width = largura;
    if (barra.className !== item.nivel) barra.className = item.nivel;
    const temp = caixa.querySelector("em");
    if (temp) {
      escreverSeMudou(temp, item.temp);
      if (temp.className !== item.calor) temp.className = item.calor;
    }
    if (caixa.title !== item.dica) caixa.title = item.dica;
  }
  pintarUsoDasAbas(dados.abas || {}, { ram: dados.ram && dados.ram.total, vram: dados.vram && dados.vram.total });
  aplicarRecursos();
}

function aplicarRecursos() {
  const ligado = cfg.mostrarRecursos !== false;
  el("recursos").hidden = !ligado || !recursosComLeitura;
  // desligado, o gasto de cada aba sai junto com a barra; ligado de novo, volta na proxima leitura
  if (!ligado) pintarUsoDasAbas({});
  const linha = el("linha-recursos");
  // no sistema em que nao ha o que mostrar, o ajuste tambem nao aparece
  if (recursosComLeitura) linha.hidden = false;
}

function pedirRecursos() {
  // com a janela escondida ninguem esta olhando, e desligado nao ha por que ler
  if (!bridge || document.hidden || cfg.mostrarRecursos === false) return;
  bridge.pedirRecursos();
}

function ligarRecursos() {
  bridge.recursosProntos.connect(pintarRecursos);
  pedirRecursos();
  setInterval(pedirRecursos, RECURSOS_A_CADA_MS);
}

/* ------------------------------------------------------ limite do plano */

let limitesPorPerfil = {};
let limitesPorAba = {};

function curtoBarra(n) {
  if (!n) return "0";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1000) return (n / 1000).toFixed(0) + "k";
  return String(n);
}

const CORES_MODELO = {
  opus: "#b088f0",
  sonnet: "#56c2c9",
  haiku: "#6cc98a",
  fable: "#d5b06b",
};

function sessaoDaAba() {
  if (!abaAtiva || abaAtiva.ehPainel) return null;
  return sessoes.find((s) => abaDaSessao(s) === abaAtiva) || null;
}

function nomeBonito(bruto) {
  return nomeDeModelo(bruto);
}

function nomeCurtoDoModelo(nome) {
  // "Sonnet 4.5 (1M context)" quebrava em tres linhas na barra; a janela vira um sufixo curto
  return nome.replace(/\s*\((\d+[MK]) context\)/i, " · $1");
}

function leituraDaAba(aba) {
  // sem programa no shell nao ha Claude na aba: a ultima leitura era da conversa que acabou, e
  // o titulo, o contexto e o modelo dela ficavam no cartao e na barra
  if (!aba || aba.ehPainel || !aba.sessionId || aba.programaVivo === false) return null;
  return limitesPorAba[aba.sessionId] || null;
}

function detalharContexto() {
  const caixa = el("medidor-ctx");
  const relogio = caixa.querySelector("u");
  const viva = leituraDaAba(abaAtiva);
  if (!viva || typeof viva.contextoTokens !== "number" || !viva.contextoTeto) {
    if (relogio) relogio.textContent = "";
    return;
  }
  // o quanto ja foi e o tamanho da janela: e o que sobra antes de o Claude compactar sozinho
  relogio.textContent = curtoBarra(viva.contextoTokens) + "/" + curtoBarra(viva.contextoTeto);
  caixa.title =
    curtoBarra(viva.contextoTokens) + " de " + curtoBarra(viva.contextoTeto) + " tokens de janela, " +
    (typeof viva.contextoSobra === "number" ? "sobram " + Math.round(viva.contextoSobra) + "% até compactar" : "");
}

function tituloVivoDaAba(aba) {
  const viva = leituraDaAba(aba);
  return viva && viva.titulo ? viva.titulo : "";
}

function pintarModelo() {
  const caixa = el("modelo-atual");
  const viva = leituraDaAba(abaAtiva);
  const s = sessaoDaAba();
  const nome = nomeBonito((viva && viva.modelo) || (s && s.modelo) || "");
  caixa.hidden = !nome;
  if (!nome) return;
  const familia = Object.keys(CORES_MODELO).find((f) => nome.toLowerCase().includes(f));
  caixa.querySelector("b").textContent = nomeCurtoDoModelo(nome);
  caixa.querySelector("i").style.background = CORES_MODELO[familia] || "#5b6371";
  caixa.title = nome + ", o modelo da conversa desta aba";
}

function contextoDaAba() {
  // contexto e por sessao, nao por conta: e a conversa daquela aba que enche a janela
  if (!abaAtiva || abaAtiva.ehPainel) return null;
  const viva = leituraDaAba(abaAtiva);
  if (viva && typeof viva.contexto === "number") return { usado: viva.contexto };
  const minha = sessoes.find((s) => typeof s.contexto === "number" && abaDaSessao(s) === abaAtiva);
  return minha ? { usado: minha.contexto } : null;
}

function contaPadraoCadastrada() {
  /* Aba sem conta escolhida usa a pasta de sempre. Se essa pasta virou uma conta cadastrada,
     ela precisa aparecer com o nome que voce deu, e nao como "Principal" em paralelo. */
  return perfis.find((p) => p.padrao) || null;
}

function chaveDaAba() {
  if (abaAtiva && !abaAtiva.ehPainel && abaAtiva.perfil) return abaAtiva.perfil.id;
  const cadastrada = contaPadraoCadastrada();
  return cadastrada ? cadastrada.id : "__padrao__";
}

function faltaPara(quando) {
  if (!quando) return "";
  // o Claude manda ora um texto ISO, ora um epoch em segundos
  const marca =
    typeof quando === "number" ? (quando < 1e12 ? quando * 1000 : quando) : new Date(quando).getTime();
  const alvo = marca - Date.now();
  if (!isFinite(alvo) || alvo <= 0) return "";
  // arredonda o total antes de separar as horas: com 1h59m50s pela frente saia 1h60m
  const minutos = Math.round(alvo / 60000);
  const h = Math.floor(minutos / 60);
  const m = minutos % 60;
  return h >= 24 ? Math.floor(h / 24) + "d" + (h % 24) + "h" : h ? h + "h" + m + "m" : m + "m";
}

function pintarMedidor(id, faixa, semLogin, dicaSemLeitura) {
  const caixa = el(id);
  const trilho = caixa.querySelector("b");
  const numero = caixa.querySelector("em");
  const pct = faixa && typeof faixa.usado === "number" ? faixa.usado : null;

  caixa.classList.remove("atencao", "critico", "sem-dado");
  if (pct === null) {
    trilho.style.width = "0%";
    numero.textContent = semLogin ? "Sem login" : "Sem leitura";
    const vazio = caixa.querySelector("u");
    if (vazio) vazio.textContent = "";
    caixa.classList.add("sem-dado");
    caixa.title = semLogin
      ? "Esta conta ainda não fez login nesta máquina, então não existe limite pra ler. Faça o login na aba dela."
      : dicaSemLeitura ||
        "Ainda sem leitura. O percentual do plano só chega junto com uma resposta do Claude, " +
          "então ele aparece depois da primeira mensagem numa aba desta conta.";
    return;
  }
  trilho.style.width = Math.min(pct, 100) + "%";
  numero.textContent = Math.round(pct) + "%";
  if (pct >= 85) caixa.classList.add("critico");
  else if (pct >= 60) caixa.classList.add("atencao");
  const zera = faltaPara(faixa.zera);
  const relogio = caixa.querySelector("u");
  if (relogio) relogio.textContent = zera || "";
  caixa.title = Math.round(pct) + "% usado" + (zera ? ", zera daqui a " + zera : "");
}

function principalSemLeitura(perfil) {
  // a conta de sempre, cadastrada ou nao, so manda o limite depois de ligada em Contas
  return leituraPrincipal === false && (!perfil || !!perfil.padrao);
}

function pintarPontoDoUso(faixas) {
  /* O ponto do botao Uso era azul fixo, com cara de tudo certo mesmo com a conta em 90%. */
  const ponto = document.querySelector("#alternar-uso .pilula-ponto");
  if (!ponto) return;
  const usados = [faixas.five_hour, faixas.seven_day]
    .map((f) => (f && typeof f.usado === "number" ? f.usado : null))
    .filter((v) => v !== null);
  const pior = usados.length ? Math.max(...usados) : null;
  ponto.className = "pilula-ponto" + (pior === null ? " sem-dado" : pior >= 85 ? " critico" : pior >= 60 ? " atencao" : " ok");
}

function pintarUsoDaBarra() {
  const barra = el("uso-barra");
  // sem aba de terminal aberta nao ha conta em uso, e mostrar limite ali seria invencao. Na
  // grade a barra fica e segue o painel ativo: escondida, com dois paineis na janela padrao o
  // limite do plano sumia de todo lugar
  const semAba = !abaAtiva || abaAtiva.ehPainel;
  const estava = barra.hidden;
  const chave = chaveDaAba();
  const perfil = perfis.find((p) => p.id === chave);
  // sem login nao existe limite pra mostrar, e emprestar o de outra conta so confunde
  const semLogin = !!perfil && !perfil.logado;
  const faixas = semLogin ? {} : (limitesPorPerfil[chave] || {}).faixas || {};
  pintarPontoDoUso(semAba ? {} : faixas);
  if (!cfg.mostrarUsoNaBarra || semAba) {
    barra.hidden = true;
    if (!estava) refazerEncaixe();
    return;
  }
  barra.hidden = false;
  // a barra mora embaixo do palco, entao aparecer ou sumir muda a altura do terminal
  if (estava) refazerEncaixe();

  const selo = el("uso-conta");
  selo.querySelector("b").textContent = perfil ? perfil.nome : "Principal";
  selo.querySelector("i").style.background = perfil ? perfil.cor : "#5b6371";
  selo.title = perfil
    ? "Tudo nesta barra é da conta desta aba"
    : "Principal, o login do ~/.claude, usado pelas abas sem conta escolhida";

  // o limite chega pela status line do Claude: conta de outro CLI ficava em Sem leitura pra sempre
  const outroCli = !!perfil && !!perfil.ferramenta && perfil.ferramenta !== "claude";
  // aba de shell sem leitura nenhuma mostrava dois medidores mortos; sem login, ou com a
  // Principal ainda por ligar, um medidor so ja diz tudo
  const porLigar = principalSemLeitura(perfil);
  const semNada = !semLogin && !porLigar && !faixas.five_hour && !faixas.seven_day && !sessaoDaAba();
  el("medidor-5h").hidden = outroCli || semNada;
  el("medidor-7d").hidden = outroCli || semNada || ((semLogin || porLigar) && !faixas.seven_day);
  const dica = porLigar
    ? "A Principal ainda não passa o limite pro Loom. Clique pra ligar em Configurações, Contas."
    : "";
  pintarMedidor("medidor-5h", faixas.five_hour, semLogin, dica);
  pintarMedidor("medidor-7d", faixas.seven_day, semLogin, dica);

  // contexto e da conversa, e aba sem Claude nunca vai ter; o selo da conta fica, pra altura
  // do terminal nao pular quando o Claude entra e sai
  const contexto = contextoDaAba();
  el("medidor-ctx").hidden = !contexto && !sessaoDaAba();
  pintarMedidor(
    "medidor-ctx",
    contexto,
    false,
    "O contexto é da conversa desta aba e aparece depois da primeira resposta do Claude nela."
  );
  detalharContexto();

  pintarModelo();
}

function refazerEncaixe() {
  setTimeout(() => {
    if (emGrade) ajustarTodas();
    else if (abaAtiva) abaAtiva.ajustar();
  }, 0);
}

let assinaturaLimites = "";

function atualizarLimites() {
  if (!bridge) return;
  bridge.getLimits((json) => {
    if (json === assinaturaLimites) return;
    assinaturaLimites = json;
    try {
      const lido = JSON.parse(json);
      limitesPorPerfil = lido.porConta || {};
      limitesPorAba = lido.porAba || {};
    } catch (e) {
      limitesPorPerfil = {};
      limitesPorAba = {};
    }
    pintarUsoDaBarra();
    if (emGrade) pintarGrade();
    for (const aba of ordem) {
      if (aba.ehPainel || !aba.sessao || aba.renomeando) continue;
      const nome = aba.nomeDoCartao();
      const rotulo = aba.botao.querySelector(".rotulo");
      if (nome && rotulo && rotulo.textContent !== nome) {
        rotulo.textContent = nome;
        rotulo.title = nome;
        aba.chaveRefletida = "";
      }
    }
  });
}

/* O vigia de arquivos e quem avisa de verdade: o coletor grava na pasta de sessoes e a tela
   sabe na hora. Este relogio e so rede de seguranca. Antes ele batia a cada 1,2 s e, junto,
   um getUsage a cada 30 s varria todos os transcript e regravava o cache pra encher uma
   variavel que ninguem lia. */
setInterval(() => {
  if (janelaAtiva) atualizarLimites();
}, 4000);

// o "zera daqui a 2h13m" precisa andar mesmo sem leitura nova, e isso nao custa disco
setInterval(() => {
  if (janelaAtiva) pintarUsoDaBarra();
}, 30000);

/* -------------------------------------------------------------- paleta */

/* Ctrl+Shift+P, como no VS Code e no Warp: um campo so que acha qualquer comando, aba, conta ou
   conversa pelo nome escrito pela metade. A lista e montada na hora em que abre, com o que existe
   agora, e as conversas de ate 30 dias chegam do app logo depois. */

const elPaleta = el("paleta");
const elPaletaTermo = el("paleta-termo");
const elPaletaLista = el("paleta-lista");
// as conversas dos ultimos 30 dias de cada conta; a lista de sessoes so traz as de 24 horas
let conversasRecentes = [];
let itensDaPaleta = [];
let achadosDaPaleta = [];
let escolhidoNaPaleta = 0;
let relogioDaPaleta = null;
// com a busca vazia a lista mostra so as conversas mais novas; o resto aparece digitando
const CONVERSAS_SEM_BUSCA = 8;
// a lista desenhada tem teto: com trezentas conversas, desenhar todas a cada letra pesava
const TETO_DA_PALETA = 120;

const ICONES_DA_PALETA = {
  mais: '<path d="M8 3.4v9.2M3.4 8h9.2"/>',
  terminal: '<rect x="1.8" y="2.8" width="12.4" height="10.4" rx="1.8"/><path d="M4.6 6.2 6.8 8 4.6 9.8M8.6 10h2.8"/>',
  fechar: '<path d="M4.2 4.2l7.6 7.6M11.8 4.2l-7.6 7.6"/>',
  grade:
    '<rect x="2.2" y="2.2" width="5" height="5" rx="1.1"/><rect x="8.8" y="2.2" width="5" height="5" rx="1.1"/>' +
    '<rect x="2.2" y="8.8" width="5" height="5" rx="1.1"/><rect x="8.8" y="8.8" width="5" height="5" rx="1.1"/>',
  dividir: '<rect x="2" y="3" width="12" height="10" rx="1.6"/><path d="M8 3v10"/>',
  uma: '<rect x="2.2" y="3.2" width="11.6" height="9.6" rx="1.6"/>',
  uso: '<path d="M3 13V8.6M6.3 13V5.2M9.7 13V7.4M13 13V3"/>',
  lupa: '<circle cx="7" cy="7" r="4.2"/><path d="m10.2 10.2 3.3 3.3"/>',
  conversa:
    '<path d="M2.6 4.2c0-.9.7-1.6 1.6-1.6h7.6c.9 0 1.6.7 1.6 1.6v5.2c0 .9-.7 1.6-1.6 1.6H7.2L4.4 13.2V11c-1-.1-1.8-.8-1.8-1.6z"/>',
  lateral: '<rect x="2" y="3" width="12" height="10" rx="1.6"/><path d="M6.4 3v10"/>',
  renomear: '<path d="M10.6 2.8l2.6 2.6-7.4 7.4-3.2.6.6-3.2z"/>',
};

function iconeDaPaleta(nome) {
  return (
    '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">' +
    (ICONES_DA_PALETA[nome] || ICONES_DA_PALETA.terminal) +
    "</svg>"
  );
}

function semAcento(texto) {
  const minusculo = String(texto || "").toLowerCase();
  // sem acento dos dois lados: quem digita "configuracoes" acha Configuracoes com cedilha e til
  return typeof minusculo.normalize === "function" ? minusculo.normalize("NFD").replace(/[̀-ͯ]/g, "") : minusculo;
}

const COMECO_DE_PALAVRA = /[\s\-_/\\.:,·]/;

function comecaPalavra(texto, i) {
  return i === 0 || COMECO_DE_PALAVRA.test(texto[i - 1]);
}

function casarAproximado(termo, texto) {
  /* Cada letra do termo aparece no texto, nessa ordem, como no VS Code: "nvtr" acha Nova aba em
     Trabalho. Vale mais o trecho seguido, o comeco de palavra e o comeco do texto. Devolve os
     pontos e as posicoes casadas, pro realce, ou null quando falta letra. */
  const q = semAcento(termo);
  const t = semAcento(texto);
  if (!q) return { pontos: 0, posicoes: [] };
  const direto = t.indexOf(q);
  if (direto >= 0) {
    const posicoes = [];
    for (let i = 0; i < q.length; i++) posicoes.push(direto + i);
    const pontos = 1000 + (direto === 0 ? 400 : comecaPalavra(t, direto) ? 250 : 0) - direto - t.length * 0.1;
    return { pontos, posicoes };
  }
  const posicoes = [];
  let pontos = 0;
  let de = 0;
  for (const letra of q) {
    let achou = -1;
    for (let i = de; i < t.length; i++) {
      if (t[i] !== letra) continue;
      if (achou < 0) achou = i;
      // seguida da anterior e o melhor; senao, a primeira que comeca uma palavra
      if (i === de && posicoes.length) break;
      if (comecaPalavra(t, i)) {
        achou = i;
        break;
      }
    }
    if (achou < 0) return null;
    const anterior = posicoes.length ? posicoes[posicoes.length - 1] : -1;
    pontos += 10 + (achou === anterior + 1 ? 15 : 0) + (comecaPalavra(t, achou) ? 20 : 0) - Math.min(achou - anterior - 1, 10);
    posicoes.push(achou);
    de = achou + 1;
  }
  return { pontos, posicoes };
}

function filtrarPaleta(itens, termo) {
  /* Cada palavra do termo precisa casar no rotulo ou no detalhe, em qualquer ordem. O rotulo
     vale mais, e so ele ganha realce. Sem termo, a lista vai na ordem montada, sem o que so
     aparece buscando. */
  const palavras = String(termo || "").trim().split(/\s+/).filter(Boolean);
  if (!palavras.length) {
    let conversas = 0;
    return itens
      .filter((item) => {
        if (item.soBuscando) return false;
        if (item.grupo !== "Conversas recentes") return true;
        conversas += 1;
        return conversas <= CONVERSAS_SEM_BUSCA;
      })
      .map((item) => ({ item, pontos: 0, posicoes: [] }));
  }
  const achados = [];
  itens.forEach((item, ordemDoItem) => {
    let pontos = 0;
    const posicoes = new Set();
    for (const palavra of palavras) {
      const noRotulo = casarAproximado(palavra, item.rotulo);
      const extra = casarAproximado(palavra, [item.detalhe, item.grupo, item.busca].filter(Boolean).join(" "));
      if (!noRotulo && !extra) return;
      if (noRotulo && (!extra || noRotulo.pontos >= extra.pontos * 0.6)) {
        pontos += noRotulo.pontos;
        noRotulo.posicoes.forEach((p) => posicoes.add(p));
      } else {
        pontos += extra.pontos * 0.6;
      }
    }
    achados.push({ item, pontos, posicoes: [...posicoes].sort((a, b) => a - b), ordemDoItem });
  });
  achados.sort((a, b) => b.pontos - a.pontos || a.ordemDoItem - b.ordemDoItem);
  return achados;
}

function haQuantoTempo(segundos) {
  const s = Math.max(0, Math.round(Number(segundos) || 0));
  if (s < 60) return "agora";
  if (s < 3600) return "há " + Math.round(s / 60) + " min";
  if (s < 86400) return "há " + Math.round(s / 3600) + " h";
  const dias = Math.round(s / 86400);
  return dias === 1 ? "há 1 dia" : "há " + dias + " dias";
}

function ehDoCodex(s) {
  const perfil = perfis.find((p) => p.id === s.perfil);
  return s.cli === "codex" || (!!perfil && perfil.ferramenta === "codex");
}

function conversasDaPaleta() {
  /* As da lateral, que trazem o estado de agora, por cima das de 30 dias que o app leu. Uma
     conversa so aparece uma vez por conta, a mais nova primeiro. */
  const juntas = new Map();
  for (const c of conversasRecentes) {
    if (c && c.sessao) juntas.set((c.perfil || "") + "|" + c.sessao, Object.assign({ estado: "parada" }, c));
  }
  for (const s of sessoes) {
    if (!s || !s.sessao) continue;
    const chave = (s.perfil || "") + "|" + s.sessao;
    juntas.set(chave, Object.assign({}, juntas.get(chave) || {}, s));
  }
  return [...juntas.values()].sort((a, b) => (a.idade || 0) - (b.idade || 0));
}

function irParaConversa(s) {
  /* Aberta numa aba, vai pra ela. A aba reaberta parada no prompt com essa conversa retoma ali
     mesmo. Fora isso, abre uma aba na conta certa ja dentro da conversa. */
  const aba = abaDaSessao(s);
  if (aba) {
    ativar(aba);
    return;
  }
  const parada = ordem.find((a) => !a.ehPainel && a.viva && a.conversaAnterior === s.sessao && !a.sessao);
  if (parada) {
    retomarAntes(parada);
    return;
  }
  retomarSessao(s);
}

const TEXTO_DO_ESTADO_NA_PALETA = { pensando: "pensando", rodando: "trabalhando", aguardando: "esperando você" };

function itensDaPaletaAgora() {
  const itens = [];
  const terminais = ordem.filter((a) => !a.ehPainel);
  const temTerminal = !!abaAtiva && !abaAtiva.ehPainel;
  const contaDe = (p) => (p ? p.nome : "Principal");

  terminais.forEach((aba, i) => {
    const dona = aba.perfil || contaPadraoCadastrada();
    const s = aba.sessao;
    const estado = s ? TEXTO_DO_ESTADO_NA_PALETA[s.estado] || "" : aba.viva ? "" : "encerrada";
    itens.push({
      id: "aba:" + (aba.sessionId || i),
      grupo: "Abas abertas",
      icone: "terminal",
      rotulo: aba.nomeDoCartao(),
      detalhe: [contaDe(dona), nomeDaPasta(pastaDaAba(aba)), estado].filter(Boolean).join(" · "),
      cor: dona ? dona.cor : "",
      marca: aba === abaAtiva ? "Atual" : naDivisao(aba) ? "Ao lado" : "",
      atalho: i < 8 ? ["Alt", String(i + 1)] : i === terminais.length - 1 ? ["Alt", "9"] : null,
      busca: "ir para aba",
      acao: () => ativar(aba),
    });
  });

  const acao = (id, rotulo, icone, atalho, executar, extra) =>
    itens.push(Object.assign({ id, grupo: "Ações", rotulo, icone, atalho, acao: executar }, extra || {}));
  const shellPadrao = shells.find((s) => s.id === cfg.shellPadrao) || shells[0];
  const contaPadrao = contaPadraoDoMenu();
  acao("nova", "Nova aba", "mais", ["Ctrl", "Shift", "T"], abaPadrao, {
    detalhe: [shellPadrao ? shellPadrao.nome : "", contaPadrao ? contaPadrao.nome : ""].filter(Boolean).join(" · "),
  });
  if (divisao) {
    acao("desfazer", "Desfazer a divisão", "uma", ["Ctrl", "Shift", "D"], desfazerDivisao, { busca: "dividir tela" });
  } else if (temTerminal) {
    const parceira = parceiraDaDivisao();
    acao("dividir", "Dividir a tela em dois", "dividir", ["Ctrl", "Shift", "D"], alternarDivisao, {
      detalhe: parceira ? "Com " + parceira.nomeDoCartao() : "Com uma aba nova igual a esta",
      busca: "lado a lado",
    });
  }
  if (emGrade || terminais.length > 1) {
    acao("grade", emGrade ? "Ver uma aba por vez" : "Ver todos lado a lado", emGrade ? "uma" : "grade", ["Ctrl", "Shift", "G"], () => alternarGrade(), {
      busca: "grade",
    });
  }
  if (temTerminal) {
    acao("buscar", "Buscar na saída", "lupa", ["Ctrl", "Shift", "F"], abrirBusca, { busca: "procurar texto" });
    acao("renomear", "Renomear a aba", "renomear", null, () => renomearAba(abaAtiva), { detalhe: abaAtiva.nomeDoCartao() });
    acao("fechar", "Fechar a aba", "fechar", ["Ctrl", "Shift", "W"], () => fecharComCuidado(abaAtiva), {
      detalhe: abaAtiva.nomeDoCartao(),
    });
  }
  acao("uso", "Mostrar ou esconder o painel de uso", "uso", ["Ctrl", "Shift", "U"], () => PainelUso.alternar(), {
    busca: "limite plano",
  });
  acao("lateral", "Mostrar ou esconder a lista de abas", "lateral", null, alternarLateral, { busca: "lateral" });
  if (!divisao && temTerminal) {
    for (const aba of terminais) {
      if (aba === abaAtiva) continue;
      acao("dividir:" + (aba.sessionId || aba.nomeDoCartao()), "Dividir com " + aba.nomeDoCartao(), "dividir", null, () => dividirCom(aba), {
        soBuscando: true,
        busca: "lado a lado tela",
      });
    }
  }

  // as conversas vem logo depois das acoes: e o que mais se procura depois das abas abertas
  for (const s of conversasDaPaleta()) {
    const perfil = perfis.find((p) => p.id === contaDaSessao(s));
    const codex = ehDoCodex(s);
    const ferramenta = perfil ? perfil.ferramentaNome : codex ? "Codex" : "Claude Code";
    const aberta = abaDaSessao(s);
    itens.push({
      id: "conversa:" + (s.perfil || "") + ":" + s.sessao,
      grupo: "Conversas recentes",
      icone: "conversa",
      rotulo: tituloDaConversa(s, ferramenta),
      detalhe: [contaDe(perfil), nomeDaPasta(s.pasta), haQuantoTempo(s.idade), TEXTO_DO_ESTADO_NA_PALETA[s.estado] || ""]
        .filter(Boolean)
        .join(" · "),
      cor: perfil ? perfil.cor : "",
      marca: aberta ? "Aberta" : codex ? "Codex" : "",
      busca: ferramenta + " conversa " + (s.pasta || ""),
      acao: () => irParaConversa(s),
    });
  }

  // nova aba em cada conta e em cada shell; a combinacao das duas so aparece buscando
  const contas = perfis.map((p) => ({ id: p.id, nome: p.nome, cor: p.cor, ferramenta: p.ferramentaNome, logado: p.logado }));
  if (!contaPadraoCadastrada()) contas.unshift({ id: "", nome: "Principal", cor: "", ferramenta: "Login do ~/.claude", logado: true });
  for (const c of contas) {
    itens.push({
      id: "nova-conta:" + c.id,
      grupo: "Nova aba",
      icone: "mais",
      rotulo: "Nova aba em " + c.nome,
      detalhe: [c.ferramenta, c.logado ? "" : "Sem login"].filter(Boolean).join(" · "),
      cor: c.cor,
      acao: () => novaAba("", c.id),
    });
  }
  for (const s of shells) {
    itens.push({
      id: "nova-shell:" + s.id,
      grupo: "Nova aba",
      icone: "terminal",
      rotulo: "Nova aba no " + s.nome,
      detalhe: contaPadrao ? contaPadrao.nome : "",
      acao: () => novaAba(s.id, contaPadrao ? contaPadrao.id : ""),
    });
    if (shells.length < 2) continue;
    for (const c of contas) {
      itens.push({
        id: "nova:" + c.id + ":" + s.id,
        grupo: "Nova aba",
        icone: "mais",
        rotulo: "Nova aba em " + c.nome + " no " + s.nome,
        detalhe: c.ferramenta,
        cor: c.cor,
        soBuscando: true,
        acao: () => novaAba(s.id, c.id),
      });
    }
  }

  const contasDoUso = perfis.map((p) => ({ nome: p.nome, cor: p.cor, id: (p.ferramenta || "claude") + ":" + p.id }));
  if (!contaPadraoCadastrada()) contasDoUso.unshift({ nome: "Principal", cor: "", id: "claude:padrao" });
  for (const c of contasDoUso) {
    itens.push({
      id: "uso:" + c.id,
      grupo: "Painel de uso",
      icone: "uso",
      rotulo: "Uso da conta " + c.nome,
      cor: c.cor,
      busca: "limite plano painel",
      acao: () => PainelUso.mostrarConta(c.id),
    });
  }

  // as secoes saem do proprio menu das configuracoes, pra paleta nunca oferecer uma que sumiu
  Array.from(document.querySelectorAll(".nav-item")).forEach((b, i) => {
    const icone = b.querySelector("svg");
    itens.push({
      id: "config:" + b.dataset.alvo,
      grupo: "Configurações",
      rotulo: "Configurações, " + b.textContent.trim(),
      svg: icone ? icone.outerHTML : "",
      atalho: i === 0 ? ["Ctrl", ","] : null,
      busca: "ajustes preferencias",
      acao: () => abrirConfig(b.dataset.alvo),
    });
  });
  return itens;
}

function rotuloComRealce(alvo, texto, posicoes) {
  /* O texto vai como texto, nunca como HTML: o titulo da conversa vem do transcript. */
  alvo.textContent = "";
  const marcadas = new Set(posicoes);
  let trecho = "";
  let realce = false;
  const fechar = () => {
    if (!trecho) return;
    if (realce) {
      const b = document.createElement("b");
      b.textContent = trecho;
      alvo.appendChild(b);
    } else alvo.appendChild(document.createTextNode(trecho));
    trecho = "";
  };
  // a posicao conta em unidades do texto sem acento, que tem o mesmo tamanho do original
  for (let i = 0; i < texto.length; i++) {
    const marcada = marcadas.has(i);
    if (marcada !== realce) {
      fechar();
      realce = marcada;
    }
    trecho += texto[i];
  }
  fechar();
}

function teclas(atalho) {
  return atalho.map((t) => "<kbd>" + escapar(t) + "</kbd>").join("");
}

function pintarPaleta(manterId) {
  const termo = elPaletaTermo.value;
  achadosDaPaleta = filtrarPaleta(itensDaPaleta, termo);
  if (manterId) {
    const i = achadosDaPaleta.findIndex((a) => a.item.id === manterId);
    escolhidoNaPaleta = i >= 0 ? i : 0;
  }
  escolhidoNaPaleta = Math.min(Math.max(escolhidoNaPaleta, 0), Math.max(achadosDaPaleta.length - 1, 0));
  elPaletaLista.innerHTML = "";
  if (!achadosDaPaleta.length) {
    const vazio = document.createElement("div");
    vazio.className = "paleta-vazio";
    vazio.textContent = "Nada encontrado";
    elPaletaLista.appendChild(vazio);
    elPaletaTermo.removeAttribute("aria-activedescendant");
    return;
  }
  const buscando = !!termo.trim();
  let grupoAnterior = "";
  achadosDaPaleta.slice(0, TETO_DA_PALETA).forEach((achado, i) => {
    const item = achado.item;
    if (!buscando && item.grupo !== grupoAnterior) {
      const titulo = document.createElement("div");
      titulo.className = "paleta-grupo";
      titulo.textContent = item.grupo;
      elPaletaLista.appendChild(titulo);
    }
    grupoAnterior = item.grupo;
    const linha = document.createElement("div");
    linha.className = "paleta-item";
    linha.id = "paleta-opcao-" + i;
    linha.dataset.indice = String(i);
    linha.setAttribute("role", "option");
    linha.innerHTML =
      '<span class="paleta-icone">' + (item.svg || iconeDaPaleta(item.icone)) + "</span>" +
      '<span class="paleta-textos"><span class="paleta-rotulo"></span><span class="paleta-detalhe"></span></span>' +
      '<span class="paleta-fim"></span>';
    rotuloComRealce(linha.querySelector(".paleta-rotulo"), item.rotulo, achado.posicoes);
    const detalhe = linha.querySelector(".paleta-detalhe");
    // o ponto da conta so vai junto de um texto: sozinho embaixo do rotulo parecia sobra
    if (item.detalhe) {
      if (item.cor) {
        const ponto = document.createElement("i");
        ponto.style.background = item.cor;
        detalhe.appendChild(ponto);
      }
      const texto = document.createElement("span");
      texto.textContent = item.detalhe;
      detalhe.appendChild(texto);
    }
    const fim = linha.querySelector(".paleta-fim");
    if (item.marca) {
      const marca = document.createElement("span");
      marca.className = "paleta-marca";
      marca.textContent = item.marca;
      fim.appendChild(marca);
    }
    if (item.atalho) fim.insertAdjacentHTML("beforeend", teclas(item.atalho));
    else if (buscando) {
      // buscando a lista perde os titulos dos grupos, entao cada item diz de onde vem
      const grupo = document.createElement("span");
      grupo.className = "paleta-tag";
      grupo.textContent = item.grupo;
      fim.appendChild(grupo);
    }
    // o foco fica no campo: clicar na lista nao pode tirar ele de la
    linha.addEventListener("mousedown", (ev) => ev.preventDefault());
    linha.addEventListener("mousemove", () => {
      if (escolhidoNaPaleta !== i) escolherNaPaleta(i, false);
    });
    linha.addEventListener("click", () => executarDaPaleta(i));
    elPaletaLista.appendChild(linha);
  });
  escolherNaPaleta(escolhidoNaPaleta, true);
}

function escolherNaPaleta(indice, rolar) {
  const total = Math.min(achadosDaPaleta.length, TETO_DA_PALETA);
  if (!total) return;
  escolhidoNaPaleta = ((indice % total) + total) % total;
  let alvo = null;
  for (const linha of elPaletaLista.querySelectorAll(".paleta-item")) {
    const escolhida = Number(linha.dataset.indice) === escolhidoNaPaleta;
    linha.classList.toggle("escolhido", escolhida);
    linha.setAttribute("aria-selected", escolhida ? "true" : "false");
    if (escolhida) alvo = linha;
  }
  if (!alvo) return;
  elPaletaTermo.setAttribute("aria-activedescendant", alvo.id);
  if (!rolar) return;
  // rola so a lista, na mao, como na lateral: o scrollIntoView mexeria na pagina inteira
  const lista = elPaletaLista.getBoundingClientRect();
  const caixa = alvo.getBoundingClientRect();
  // o primeiro da lista leva junto o titulo do grupo de cima
  if (escolhidoNaPaleta === 0) elPaletaLista.scrollTop = 0;
  else if (caixa.top < lista.top) elPaletaLista.scrollTop -= lista.top - caixa.top + 4;
  else if (caixa.bottom > lista.bottom) elPaletaLista.scrollTop += caixa.bottom - lista.bottom + 4;
}

function abrirPaleta() {
  if (!elPaleta.hidden) {
    elPaletaTermo.select();
    return;
  }
  fecharMenus();
  elPaleta.hidden = false;
  elPaletaTermo.value = "";
  escolhidoNaPaleta = 0;
  itensDaPaleta = itensDaPaletaAgora();
  pintarPaleta();
  elPaletaTermo.focus();
  pedirConversas(0);
}

function remontarPaleta() {
  const atual = achadosDaPaleta[escolhidoNaPaleta];
  itensDaPaleta = itensDaPaletaAgora();
  pintarPaleta(atual ? atual.item.id : "");
}

function pedirConversas(tentativa) {
  clearTimeout(relogioDaPaleta);
  if (!bridge || !bridge.listarConversas || elPaleta.hidden) return;
  bridge.listarConversas((json) => {
    let lido = null;
    try {
      lido = JSON.parse(json);
    } catch (e) {
      lido = null;
    }
    if (!lido || elPaleta.hidden) return;
    conversasRecentes = Array.isArray(lido.conversas) ? lido.conversas : [];
    remontarPaleta();
    // a leitura nova ainda anda: pergunta de novo, com teto, pra nunca virar relogio sem fim
    if (lido.coletando && tentativa < 20) relogioDaPaleta = setTimeout(() => pedirConversas(tentativa + 1), 500);
  });
}

function fecharPaleta() {
  if (elPaleta.hidden) return;
  elPaleta.hidden = true;
  clearTimeout(relogioDaPaleta);
  if (!veuAberto() && abaAtiva) abaAtiva.foco();
}

function executarDaPaleta(indice) {
  const achado = achadosDaPaleta[indice];
  if (!achado) return;
  fecharPaleta();
  try {
    achado.item.acao();
  } catch (e) {
    console.error("o comando da paleta falhou", e);
  }
}

elPaletaTermo.addEventListener("input", () => {
  escolhidoNaPaleta = 0;
  pintarPaleta();
});

elPaletaTermo.addEventListener("keydown", (ev) => {
  // nada daqui sobe pra janela: o Esc nao pode interromper o Claude nem o Ctrl+Shift+W fechar a aba
  ev.stopPropagation();
  const passo = { ArrowDown: 1, ArrowUp: -1, PageDown: 8, PageUp: -8 }[ev.key];
  if (passo) {
    ev.preventDefault();
    const total = Math.min(achadosDaPaleta.length, TETO_DA_PALETA);
    // a pagina para na ponta; a seta da a volta, como no VS Code
    const destino = Math.abs(passo) > 1 ? Math.min(Math.max(escolhidoNaPaleta + passo, 0), total - 1) : escolhidoNaPaleta + passo;
    escolherNaPaleta(destino, true);
  } else if (ev.key === "Enter") {
    ev.preventDefault();
    executarDaPaleta(escolhidoNaPaleta);
  } else if (ev.key === "Escape" || (ev.ctrlKey && ev.shiftKey && ev.code === "KeyP")) {
    ev.preventDefault();
    fecharPaleta();
  } else if (ev.key === "Tab") {
    ev.preventDefault();
  }
});

// clicar fora da caixa fecha, como o menu
elPaleta.addEventListener("mousedown", (ev) => {
  if (ev.target === elPaleta) {
    ev.preventDefault();
    fecharPaleta();
  }
});

/* ------------------------------------------------------------- atalhos */

window.addEventListener("keydown", (ev) => {
  // caixa e menu abertos vem antes da busca e da selecao: com uma selecao esquecida no
  // terminal de tras, o primeiro Esc so soltava a selecao e a caixa continuava ali. O menu do
  // botao direito nao fechava com Esc nenhum, e o segundo ia pro shell
  if (ev.key === "Escape") {
    const aberto = veuAberto();
    if (aberto) return fecharVeu(aberto);
    if (menuAberto()) {
      fecharMenus();
      if (abaAtiva) abaAtiva.foco();
      return;
    }
  }
  if (ev.key === "Escape" && !elBusca.hidden) {
    fecharBusca();
    return;
  }
  if (ev.key === "Escape" && abaAtiva && !abaAtiva.ehPainel && abaAtiva.term.hasSelection()) {
    abaAtiva.term.clearSelection();
    return;
  }
  if (veuAberto()) return;

  if (ev.key === "F3") {
    ev.preventDefault();
    if (elBusca.hidden) abrirBusca();
    else buscar(ev.shiftKey);
    return;
  }
  if (ev.altKey && !ev.ctrlKey && /^Digit[1-9]$/.test(ev.code)) {
    ev.preventDefault();
    pularParaAba(Number(ev.code.slice(5)));
    return;
  }

  if (ev.ctrlKey && ev.shiftKey && ev.code === "KeyT") {
    ev.preventDefault();
    abaPadrao();
  } else if (ev.ctrlKey && ev.shiftKey && ev.code === "KeyW") {
    ev.preventDefault();
    if (abaAtiva) fecharComCuidado(abaAtiva);
  } else if (ev.ctrlKey && ev.shiftKey && ev.code === "KeyF") {
    ev.preventDefault();
    abrirBusca();
  } else if (ev.ctrlKey && ev.shiftKey && ev.code === "KeyG") {
    ev.preventDefault();
    alternarGrade();
  } else if (ev.ctrlKey && ev.shiftKey && ev.code === "KeyU") {
    ev.preventDefault();
    PainelUso.alternar();
  } else if (ev.ctrlKey && ev.shiftKey && ev.code === "KeyP") {
    ev.preventDefault();
    abrirPaleta();
  } else if (ev.ctrlKey && ev.shiftKey && ev.code === "KeyD") {
    ev.preventDefault();
    alternarDivisao();
  } else if (ev.ctrlKey && !ev.shiftKey && ev.code === "Comma") {
    ev.preventDefault();
    abrirConfig();
  } else if (ev.ctrlKey && (ev.code === "Equal" || ev.code === "NumpadAdd")) {
    ev.preventDefault();
    zoom(1);
  } else if (ev.ctrlKey && ((ev.code === "Minus" && !ev.shiftKey) || ev.code === "NumpadSubtract")) {
    ev.preventDefault();
    zoom(-1);
  } else if (ev.ctrlKey && (ev.code === "Digit0" || ev.code === "Numpad0")) {
    ev.preventDefault();
    zoom(0);
  } else if (ev.ctrlKey && (ev.code === "Tab" || ev.code === "PageUp" || ev.code === "PageDown")) {
    ev.preventDefault();
    const paraTras = ev.code === "PageUp" || (ev.code === "Tab" && ev.shiftKey);
    andarNasAbas(paraTras ? -1 : 1);
  }
});

function andarNasAbas(passo) {
  if (ordem.length < 2) return;
  const i = ordem.indexOf(abaAtiva);
  ativar(ordem[(i + passo + ordem.length) % ordem.length]);
}

function pularParaAba(numero) {
  /* Alt e o numero pula direto. Alt+9 e sempre a ultima, que e o costume do Windows Terminal. */
  const terminais = ordem.filter((a) => !a.ehPainel);
  const alvo = numero === 9 ? terminais[terminais.length - 1] : terminais[numero - 1];
  if (alvo) ativar(alvo);
  else piscarAviso(numero === 9 ? "Nenhuma aba aberta" : "Não há aba " + numero);
}

const FONTE_PADRAO = 14;
const FONTE_MINIMA = 9;
const FONTE_MAXIMA = 22;

// o zoom desta vez, somado ao tamanho escolhido em Aparencia e nunca gravado
let zoomAtual = 0;

function fonteDoTerminal() {
  const base = cfg.tamanhoFonte || FONTE_PADRAO;
  return Math.min(Math.max(base + zoomAtual, FONTE_MINIMA), FONTE_MAXIMA);
}

function zoom(passo) {
  /* Ctrl com mais, menos e zero, e Ctrl com a roda. Mexe no tamanho da fonte do terminal,
     que e o que a pessoa quer dizer com zoom aqui. Gravava a cada passo e o zero voltava pra
     14 fixo: quem tinha escolhido 16 em Aparencia, esbarrava na roda e apertava Ctrl+0 pra
     desfazer, ficava com 14 salvo. Agora e um deslocamento so desta vez, e o zero tira ele. */
  const antes = fonteDoTerminal();
  const base = cfg.tamanhoFonte || FONTE_PADRAO;
  if (passo === 0) zoomAtual = 0;
  else zoomAtual = Math.min(Math.max(antes + passo, FONTE_MINIMA), FONTE_MAXIMA) - base;
  const novo = fonteDoTerminal();
  if (novo === antes) return;
  for (const a of ordem) a.aplicar();
  piscarAviso(novo === base ? "Fonte em " + novo + " px" : "Fonte em " + novo + " px, Ctrl+0 volta a " + base);
}

let pendenteResize = null;
window.addEventListener("resize", () => {
  clearTimeout(pendenteResize);
  pendenteResize = setTimeout(() => {
    if (emGrade) reencaixarGrade();
    else if (abaAtiva) abaAtiva.ajustar();
  }, 50);
});

/* ---------------------------------------------------------------- sobre */

function mostrarSobre(json) {
  // a versao vem do app, e o topo e a tela Sobre mostram a mesma que esta rodando
  try {
    const sobre = JSON.parse(json);
    el("versao-topo").textContent = sobre.versao ? "v" + sobre.versao : "";
    el("sobre-versao").textContent = sobre.versao || "";
    el("sobre-raiz").textContent = sobre.raiz || "";
  } catch (e) {
    console.warn("sobre ilegivel", e);
  }
}

/* ---------------------------------------------------------------- canal */

new QWebChannel(qt.webChannelTransport, (channel) => {
  bridge = channel.objects.bridge;

  bridge.output.connect((id, data) => {
    const aba = porSessao.get(id);
    if (aba) {
      aba.escrever(data);
      return;
    }
    // A aba ainda nao recebeu o proprio id de volta. Guardar POR SESSAO, nunca em todas as
    // que estao sem id: restaurando varias abas de uma vez, o banner de cada shell caia
    // dentro de todas as irmas e cada terminal nascia com a saida das outras.
    const fila = pendentes.get(id);
    if (fila) fila.push(data);
    else pendentes.set(id, [data]);
  });

  bridge.arquivosSoltos.connect((json) => {
    try {
      soltarArquivos(JSON.parse(json));
    } catch (e) {
      console.warn("caminho ilegivel", e);
    }
  });
  bridge.arquivosSoltosEm.connect((json, x, y) => {
    try {
      soltarArquivos(JSON.parse(json), { x, y });
    } catch (e) {
      console.warn("caminho ilegivel", e);
    }
  });

  bridge.mexeram.connect(() => {
    atualizarSessoes();
    atualizarLimites();
  });
  bridge.sessoesProntas.connect(receberSessoes);
  // a prova do sudo roda fora da thread da tela e pode chegar depois da primeira pergunta
  bridge.elevacaoMudou.connect(receberElevacao);

  // clique na notificacao do Windows: a aba que avisou, e nao a que estava aberta
  bridge.irParaAba.connect((id) => {
    const aba = porSessao.get(id);
    if (aba && ordem.indexOf(aba) >= 0) ativar(aba);
  });

  // o e-mail e o plano chegam depois, perguntados ao CLI fora da thread da tela
  bridge.perfisMudaram.connect(() => {
    recarregarPerfis(() => {
      if (!elMenu.hidden) montarMenu();
      const contas = el("lista-contas");
      const editando = contas && contas.contains(document.activeElement);
      if (!el("veu-config").hidden && !editando) montarContas();
    });
  });

  bridge.sobre(mostrarSobre);
  // antes de qualquer aba: as respostas do canal chegam na ordem dos pedidos, e a primeira aba a
  // aparecer ja precisa saber se ha placa de video pra ligar o WebGL
  bridge.desenho(receberDesenho);

  bridge.exited.connect((id, code) => {
    pendentes.delete(id);
    const aba = porSessao.get(id);
    if (aba) {
      aba.encerrada(code);
      if (cfg.fecharAoSair && code === 0) {
        fechar(aba);
        return;
      }
      montarSessoes();
    }
  });

  bridge.janelaPropria(ligarJanelaPropria);
  bridge.getHome((casa) => {
    pastaPessoal = (casa || "").replace(/[\\/]+$/, "");
  });
  // se a conta de sempre passa pelo coletor: sem isso a barra prometia uma leitura que nunca vem
  montarLeituraPrincipal();

  bridge.getConfig((jsonCfg) => {
    cfg = JSON.parse(jsonCfg);
    bridge.elevacao(receberElevacao);

    bridge.listShells((jsonShells) => {
      shells = JSON.parse(jsonShells);
      carregarFerramentas(() => {
        bridge.listProfiles((jsonPerfis) => {
          perfis = JSON.parse(jsonPerfis);
          montarSelecaoShells();
          montarSelecaoPerfis();
          montarSelecaoFerramentas();
          preencherCampos();
          ligarCampos();
          aplicarLarguraLateral();
          ligarRecursos();
          // antes das abas: o terminal ja nasce na largura que sobra ao lado do painel
          PainelUso.ligar();
          reconectarAbas((reconectou) => {
            if (reconectou) return;
            restaurarAbas((tinha) => {
              if (!tinha) abaPadrao();
            });
          });
          atualizarSessoes();
          atualizarLimites();

          const parametros = new URLSearchParams(location.search);
          const secaoDemo = parametros.get("config");
          if (secaoDemo) setTimeout(() => abrirConfig(secaoDemo), 1800);
          const termoDemo = parametros.get("busca");
          if (termoDemo) {
            setTimeout(() => {
              abrirBusca();
              elBuscaTermo.value = termoDemo;
              buscar(false);
            }, 1500);
          }
          const demo = parseInt(parametros.get("demo") || "0", 10);
          if (demo > 1) {
            for (let i = 1; i < demo; i++) novaAba(cfg.shellPadrao || shells[0].id, "");
            alternarGrade(true);
          }
        });
      });
    });
  });
});

/* Recarregar a pagina NAO fecha os shells: eles moram no app, e a pagina nova se liga a eles
   de volta. Fechar a janela e que encerra tudo, pelo lado do app. */

function pulsar() {
  /* Prova pro app que a pagina continua sendo desenhada. O requestAnimationFrame so roda
     quando um quadro sai de verdade, entao pagina travada ou motor que parou de desenhar
     param de pulsar, e a vigia do app refaz a tela sozinha. Um quadro por segundo, nao mais. */
  requestAnimationFrame(() => {
    if (bridge) bridge.pulso();
    setTimeout(pulsar, 1000);
  });
}
pulsar();
