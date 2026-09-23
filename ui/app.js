"use strict";


const TEMA = {
  background: "#16181c",
  foreground: "#d3d8e0",
  cursor: "#4f9cf9",
  cursorAccent: "#16181c",
  // com o fundo em cinza, o azul antigo quase nao aparecia por cima do texto
  selectionBackground: "#3c5a80",
  selectionForeground: "#eef2f8",
  selectionInactiveBackground: "#2c3d52",
  black: "#16181c", red: "#e05561", green: "#6cc98a", yellow: "#d5b06b",
  blue: "#4f9cf9", magenta: "#b088f0", cyan: "#56c2c9", white: "#c8cfda",
  brightBlack: "#5b6371", brightRed: "#f0808a", brightGreen: "#8ce0a8",
  brightYellow: "#f0cd85", brightBlue: "#7fb8ff", brightMagenta: "#cba8ff",
  brightCyan: "#87dce2", brightWhite: "#eef2f8",
};

/* Teclas que a janela trata e o terminal NAO pode receber. Sem isto o xterm manda o
   caractere pro shell e a janela executa o atalho, os dois ao mesmo tempo. */
const ATALHOS_DA_JANELA = new Set(["KeyT", "KeyW", "KeyG"]);
const ATALHOS_DE_CONTROLE = new Set([
  "Comma", "Tab", "KeyF", "PageUp", "PageDown",
  "Equal", "Minus", "Digit0", "NumpadAdd", "NumpadSubtract", "Numpad0",
]);

function pilhaDeFonte(nome) {
  const escolhida = nome || "Hack";
  return '"' + escolhida + '", Hack, "JetBrains Mono", Consolas, monospace';
}

/* Caminho de arquivo com numero de linha, que e o formato que o Claude Code imprime o dia
   inteiro. Aceita caminho do Windows e do Unix, com ou sem coluna no fim. */
const ARQUIVO_COM_LINHA =
  /(?:[A-Za-z]:[\\/])?(?:[\w.@~-]+[\\/])*[\w.@-]+\.[A-Za-z0-9]{1,8}:\d+(?::\d+)?/g;

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
        const inicio = m.index + 1;
        achados.push({
          range: { start: { x: inicio, y: numero }, end: { x: inicio + m[0].length - 1, y: numero } },
          text: m[0],
          activate: (ev, alvo) => abrirExterno(alvo, aba),
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
const porSessao = new Map();
// saida que chegou antes de a aba saber o proprio id, guardada por sessao
const pendentes = new Map();
const ordem = [];
let abaAtiva = null;
let removendo = null;

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

function opcoesTerminal() {
  return {
    theme: TEMA,
    fontFamily: pilhaDeFonte(cfg.fonte),
    fontSize: cfg.tamanhoFonte,
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
      new WebLinksAddon.WebLinksAddon((ev, uri) => abrirExterno(uri, this))
    );
    this.term.registerLinkProvider(provedorDeArquivos(this));
    // a pasta atual do shell, pros links relativos de uma aba sem Claude. Devolver false deixa
    // o OSC seguir pra quem mais quiser ler, inclusive o 9 que nao e de pasta
    this.term.parser.registerOscHandler(7, (dado) => {
      const pasta = pastaDoOsc(dado);
      if (pasta) this.pastaAtual = pasta;
      return false;
    });
    this.term.parser.registerOscHandler(9, (dado) => {
      if (String(dado).startsWith("9;")) {
        const pasta = pastaDoOsc(String(dado).slice(2));
        if (pasta) this.pastaAtual = pasta;
      }
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

    this.term.open(this.tela);
    this.tela.addEventListener("mousedown", () => {
      if (emGrade && abaAtiva !== this) ativar(this);
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
    try {
      this.webgl = new WebglAddon.WebglAddon();
      // o Chromium so mantem 16 contextos WebGL por pagina e derruba o mais antigo sem avisar.
      // Sem tratar, aquele terminal congelava no ultimo quadro pra sempre; descartando o addon,
      // o xterm volta sozinho pro desenho por DOM e a aba continua viva.
      this.webgl.onContextLoss(() => {
        this.webgl.dispose();
        this.webgl = null;
      });
      this.term.loadAddon(this.webgl);
    } catch (e) {
      this.webgl = null;
      console.warn("sem webgl, caiu no canvas", e);
    }

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
    });
    this.term.onResize((dim) => {
      if (bridge && this.sessionId) bridge.resize(this.sessionId, dim.cols, dim.rows);
    });
    this.term.attachCustomKeyEventHandler((ev) => {
      // Com uma caixa aberta nenhuma tecla vai pro shell. O xterm segura o Esc e o Enter e a
      // janela nem ficava sabendo: o Esc pra cancelar interrompia o Claude e a caixa ficava.
      // Devolvendo false a tecla sobe ate a janela, que fecha a caixa.
      if (veuAberto()) return false;
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
      if (this.sessao.titulo) return this.sessao.titulo;
      const dona = this.perfil || contaPadraoCadastrada();
      return dona ? dona.ferramentaNome : "Claude Code";
    }
    if (this.tituloOsc) return this.tituloOsc;
    return this.shell.nome + (this.elevado ? " (adm)" : "");
  }

  pintar() {
    const dona = this.perfil || contaPadraoCadastrada();
    const ponto = this.botao.querySelector(".ponto");
    const cor = dona ? dona.cor : "#5b6371";
    this.botao.style.borderLeftColor = this.viva ? cor : "#2a2f38";
    ponto.style.background = this.viva ? cor : "#3a3f49";

    const rodape = this.botao.querySelector(".sessao-rodape");
    rodape.innerHTML = "";
    const conta = document.createElement("span");
    conta.className = "sessao-conta";
    conta.textContent = dona ? dona.nome : "Principal";
    if (dona) conta.style.color = dona.cor;
    rodape.appendChild(conta);
    if (!this.sessao) {
      const shell = document.createElement("span");
      shell.textContent = this.shell.nome;
      rodape.appendChild(shell);
    }

    if (!this.sessao) {
      this.botao.classList.remove("pensando", "rodando", "aguardando", "parada");
      const titulo = this.botao.querySelector(".rotulo");
      const nome = this.nomeDoCartao();
      titulo.textContent = nome;
      titulo.title = this.tituloOsc && !this.apelido ? this.shell.nome : "";
      const origem = this.botao.querySelector(".sessao-origem");
      if (origem) origem.textContent = "";
      this.botao.querySelector(".sessao-linha").textContent = this.viva ? "shell" : "closed";
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
          s.estado, s.titulo, s.ferramenta, s.alvo, s.pasta, s.contexto, s.modelo,
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
    const ferramenta = dona ? dona.ferramentaNome : "Claude Code";
    const nome = this.nomeDoCartao();
    const titulo = cartao.querySelector(".rotulo");
    titulo.textContent = nome;
    titulo.title = nome;
    pintarOrigem(cartao, ferramenta, s);
    cartao.querySelector(".sessao-idade").textContent = tempoCurto(s.idade);

    const linha = cartao.querySelector(".sessao-linha");
    linha.innerHTML = "";
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

    this.pintar();
    if (typeof s.contexto === "number") {
      cartao.querySelector(".sessao-rodape").appendChild(barraContexto(s.contexto));
    }
  }

  abrir(pedido) {
    this.pedidoInicial = pedido || null;
    ativar(this);
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
        JSON.stringify(Object.assign({}, pedido, { elevar: this.elevado })),
        (id) => {
          if (this.destruida) {
            // a aba fechou antes de o id voltar: o shell ja tinha subido e ficava vivo sem
            // tela ate o app fechar, com o CLI da conta digitado dentro dele logo depois
            bridge.closeSession(id);
            pendentes.delete(id);
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
    // a pasta de onde ela nasceu; a de agora o shell morto nao sabe dizer.
    // O /login nao se repete: ele era pedido daquela primeira abertura, nao desta.
    const pedido = Object.assign({}, this.pedidoInicial);
    delete pedido.entrar;
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
    if (mudou) this.fit.fit();
  }

  destruir() {
    this.destruida = true;
    if (bridge && this.sessionId && this.viva) bridge.closeSession(this.sessionId);
    if (this.sessionId) porSessao.delete(this.sessionId);
    this.term.dispose();
    super.destruir();
  }
}

/* ------------------------------------------------------------- controle */

function ativar(aba) {
  if (abaAtiva === aba) return;
  if (emGrade && aba.ehPainel) alternarGrade(false);
  const saindo = abaAtiva;
  if (abaAtiva) {
    abaAtiva.tela.classList.remove("ativa");
    abaAtiva.botao.classList.remove("ativa");
  }
  abaAtiva = aba;
  aba.tela.classList.add("ativa");
  aba.botao.classList.add("ativa");
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
}

function fechar(aba) {
  const i = ordem.indexOf(aba);
  const eraAtiva = abaAtiva === aba;
  aba.destruir();
  atualizarVazio();
  if (eraAtiva) {
    abaAtiva = null;
    const alvo = ordem[Math.min(i, ordem.length - 1)];
    if (alvo) ativar(alvo);
  }
  montarSessoes();
  anotarAbas();
  pintarUsoDaBarra();
  if (emGrade) {
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
     motor web caiu ou porque o Loom foi aberto de novo, cada aba volta ligada ao shell que
     continuou rodando. No start normal a lista vem vazia e segue a restauracao de sempre. */
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
    depois(true);
  });
}

function abaPadrao() {
  novaAba(cfg.shellPadrao || (shells.length ? shells[0].id : ""), cfg.perfilPadrao || "");
}

function abrirStats() {
  abrirConfig("uso");
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

el("abrir-stats").addEventListener("click", abrirStats);
// clicar em 5h, 7d, ctx ou no nome da conta abre a tela de Uso, que e o que a pessoa espera
el("uso-barra").addEventListener("click", (ev) => {
  if (ev.target.closest(".medidor, .uso-conta, .modelo")) abrirStats();
});

/* ------------------------------------------------------------------ veu */

// quem recebe o foco quando a caixa abre: o Enter confirma o fechar, mas no remover cai no Cancelar
const FOCO_DO_VEU = {
  "veu-fechar": "#confirmar-fechamento",
  "veu-remover": '[data-fecha="veu-remover"]',
  "veu-config": ".nav-item.ativa",
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
  if (id === "veu-config") {
    medirFps(!!cfg.mostrarFps);
    clearTimeout(relogioUso);
  }
  // a caixa de nova conta fecha por cima das configuracoes, que continuam abertas
  if (!veuAberto() && abaAtiva) abaAtiva.foco();
}

function veuAberto() {
  return ["veu-conta", "veu-remover", "veu-config", "veu-fechar"].find((id) => !el(id).hidden);
}

document.querySelectorAll("[data-fecha]").forEach((b) => {
  b.addEventListener("click", () => fecharVeu(b.dataset.fecha));
});

/* ----------------------------------------------------------------- menu */

function montarMenu() {
  const alvoShells = el("menu-shells");
  alvoShells.innerHTML = "";
  for (const s of shells) {
    const b = document.createElement("button");
    b.className = "menu-item";
    b.innerHTML = '<span class="ponto" style="background:#2f3540"></span><span></span>';
    b.querySelectorAll("span")[1].textContent = s.nome;
    b.addEventListener("click", () => {
      esconderMenu();
      novaAba(s.id, "");
    });

    const escudo = document.createElement("button");
    escudo.className = "menu-escudo";
    escudo.textContent = "adm";
    escudo.title = elevacao.disponivel
      ? "Abrir este shell com privilégio de administrador"
      : TEXTO_DA_ELEVACAO[elevacao.motivo] ||
        "Precisa do sudo do Windows 11 ou do gsudo pra abrir elevado na mesma aba";
    escudo.disabled = !elevacao.disponivel;
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
};

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
      piscarAviso("Ela já está cadastrada como " + resposta.nome);
      recarregarPerfis(() => depois && depois(resposta.id));
      return;
    }
    recarregarPerfis(() => {
      montarContas();
      montarSelecaoPerfis();
      piscarAviso("Pronto, ela entrou usando o login que já existia");
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
  const criandoDoPainel = !el("veu-config").hidden;
  bridge.createProfile(nome, el("ferramenta-conta").value, () => {
    recarregarPerfis(() => {
      fecharVeu("veu-conta");
      montarContas();
      montarSelecaoPerfis();
      if (!criandoDoPainel) novaAba("", perfis[perfis.length - 1].id);
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

function montarContas() {
  const alvo = el("lista-contas");
  alvo.innerHTML = "";
  el("adotar-principal").hidden = !!contaPadraoCadastrada();
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
    cor.title = "trocar a cor";
    cor.addEventListener("click", () => {
      const paleta = ["#4f9cf9", "#63c98a", "#d9b25f", "#b088f0", "#5fc2c9", "#f08189"];
      const proxima = paleta[(paleta.indexOf(p.cor) + 1) % paleta.length];
      bridge.updateProfile(p.id, "", proxima);
      recarregarPerfis(montarContas);
    });

    const nome = document.createElement("input");
    nome.className = "conta-nome";
    nome.value = p.nome;
    nome.title = p.nome;
    nome.spellcheck = false;
    nome.addEventListener("blur", () => {
      const novo = nome.value.trim();
      if (novo && novo !== p.nome) {
        bridge.updateProfile(p.id, novo, "");
        recarregarPerfis(montarContas);
      }
    });
    nome.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") nome.blur();
    });

    const cli = document.createElement("span");
    cli.className = "conta-info conta-email";
    cli.textContent = p.email || p.ferramentaNome;
    cli.title = p.email ? p.ferramentaNome + " · " + p.configDir : p.configDir;

    const info = document.createElement("span");
    info.className = "conta-info" + (p.logado ? " on" : "");
    info.textContent = p.emprestada ? "Login de sempre" : p.logado ? "Logado" : "Sem login";
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
    esquecer.title = "apaga só a credencial, mantendo histórico e ajustes";
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
          esquecer.title = "apaga só a credencial, mantendo histórico e ajustes";
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
        recarregarPerfis(montarContas);
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

    for (const [chave, rotulo] of [["comando", "comando"], ["env", "variável de ambiente"], ["login", "arquivo de login"]]) {
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
  ["mostrarFps", "check"],
  ["shellPadrao", "select"],
  ["perfilPadrao", "select"],
  ["historico", "number"],
  ["confirmarFechar", "check"],
  ["abrirCliDaConta", "check"],
  ["mostrarUsoNaBarra", "check"],
  ["confiarPastas", "check"],
  ["restaurarAbas", "check"],
  ["esconderStatusLine", "check"],
  ["avisarQuandoPronta", "check"],
  ["fecharAoSair", "check"],
  ["editor", "text"],
];

function montarSelecaoPerfis() {
  const sel = el("cfg-perfilPadrao");
  sel.innerHTML = '<option value="">Nenhuma, shell puro</option>';
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
    if (tipo === "check") campo.checked = !!cfg[chave];
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
  el("fps").hidden = !cfg.mostrarFps;
  medirFps(!!cfg.mostrarFps || !el("veu-config").hidden);
}

function aplicarTudo() {
  for (const a of ordem) a.aplicar();
  preencherCampos();
  pintarUsoDaBarra();
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
          });
        },
        tipo === "range" ? 90 : 0
      );
    });
  }
}

document.querySelectorAll(".nav-item").forEach((b) => {
  b.addEventListener("click", () => {
    mostrarSecao(b.dataset.alvo);
    if (b.dataset.alvo === "uso") pintarUso();
  });
});

function mostrarSecao(alvo) {
  document.querySelectorAll(".nav-item").forEach((o) => o.classList.toggle("ativa", o.dataset.alvo === alvo));
  document.querySelectorAll(".secao").forEach((s) => s.classList.toggle("ativa", s.id === "sec-" + alvo));
}

let relogioUso = null;

function usoAVista() {
  return !el("veu-config").hidden && el("sec-uso").classList.contains("ativa");
}

function pintarUso() {
  const alvo = el("corpo-uso");
  if (!alvo) return;
  clearTimeout(relogioUso);
  bridge.getUsage((json) => {
    let dados = {};
    try {
      dados = JSON.parse(json);
    } catch (e) {
      dados = {};
    }
    const porConta = dados && dados.porConta ? dados.porConta : {};
    const parcial = !!(dados && dados.parcial);
    renderStats(porConta, perfis, limitesPorPerfil, parcial);
    // Com a tela aberta os numeros andam. Historico grande entra aos poucos, uma passada por
    // vez, entao o resto e pedido logo em vez de esperar a pessoa fechar e abrir de novo.
    clearTimeout(relogioUso);
    if (usoAVista()) {
      relogioUso = setTimeout(() => {
        if (usoAVista()) pintarUso();
      }, parcial ? 1500 : 30000);
    }
  });
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
      if (secao) mostrarSecao(secao);
      abrirVeu("veu-config");
      medirFps(true);
      // a varredura do historico so com a tela de Uso a vista: o Ctrl+virgula pra mexer na
      // fonte disparava a leitura de todas as contas e segurava os terminais enquanto rodava
      if (usoAVista()) pintarUso();
    });
  });
}

el("abrir-config").addEventListener("click", () => abrirConfig());
el("vazio-nova").addEventListener("click", abaPadrao);

/* ------------------------------------------------------------- sessoes */

const elLateral = el("lateral");
const elListaSessoes = el("abas");
const elListaExternas = el("lista-externas");
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

// a conversa cujo terminal ja fechou: o Claude dela morreu junto com o shell
const ENCERRADA = { texto: "closed", dica: "O terminal desta conversa foi fechado. Clique pra retomar numa aba." };

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
    return Object.assign({}, s, { estado: s.bruto });
  }
  if (s.estado === "parada" && s.bruto === "respondeu") {
    return Object.assign({}, s, { estado: "aguardando" });
  }
  return s;
}

let sessaoDoMenu = null;

let pastaPessoal = "";

function contaDaSessao(s) {
  /* A pasta onde o transcript esta e a verdade sobre de quem e a sessao. A marca gravada
     na hora pode ser antiga, de antes de a conta ser renomeada ou cadastrada. */
  if (s.perfil && s.perfil !== "__padrao__") return s.perfil;
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

function pintarOrigem(cartao, ferramenta, s) {
  /* Abaixo do titulo, quem gerou a conversa e onde ela roda. */
  const alvo = cartao.querySelector(".sessao-origem");
  if (!alvo) return;
  alvo.innerHTML = "<b></b><i></i>";
  alvo.querySelector("b").textContent = ferramenta;
  const pasta = nomeDaPasta(s.pasta);
  alvo.querySelector("i").textContent = pasta ? " · " + pasta : "";
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
    const exata = sessoes.find((s) => !usadas.has(s.sessao) && s.aba && s.aba === aba.sessionId);
    if (exata) {
      usadas.add(exata.sessao);
      donos.set(aba, comoNaAba(exata, aba));
    }
  }

  for (const aba of terminais) aba.refletir(aba.viva ? donos.get(aba) || null : null);
  // depois do refletir: e aqui que a aba passa a saber em que estado ela esta
  avisarProntas(terminais);
  anotarAbas();

  if (emGrade) pintarGrade();

  // A conversa que nasceu aqui e cuja aba nao existe mais sumia da lista e virava so um numero
  // no rotulo. Ela entra com as de fora, depois delas, marcada como encerrada, e o botao
  // direito retoma. A que ainda trabalha provavelmente foi retomada em outro terminal.
  const deFora = sessoes.filter((s) => !usadas.has(s.sessao) && s.terminal !== "loom");
  const orfas = sessoes.filter((s) => !usadas.has(s.sessao) && s.terminal === "loom");
  const lista = deFora.concat(orfas);

  const rotulo = el("divisor-rotulo");
  if (rotulo) {
    rotulo.textContent = "Terminais externos";
    rotulo.title = orfas.length
      ? orfas.length === 1
        ? "Inclui 1 conversa deste app cuja aba já foi fechada"
        : "Inclui " + orfas.length + " conversas deste app cujas abas já foram fechadas"
      : "";
  }
  elListaExternas.innerHTML = "";
  const mostrar = cfg.mostrarSessoesDeFora !== false;
  const temFora = lista.length > 0;

  el("divisor-fora").hidden = !temFora || !mostrar;
  elListaExternas.hidden = !temFora || !mostrar;
  const botaoMostrar = el("mostrar-fora");
  botaoMostrar.hidden = !temFora || mostrar;
  el("conta-fora").textContent = temFora ? String(lista.length) : "";

  if (mostrar) {
    for (const s of lista) elListaExternas.appendChild(montarCartao(s));
  }
  elListaExternas.classList.toggle("encolhida", !temFora);
  aplicarAlturaExternas();
}

function montarCartao(s) {
  {
    // conversa que nasceu numa aba deste app e nao tem mais aba: parada, e nao ready
    const encerrada = s.terminal === "loom" && (s.estado === "aguardando" || s.estado === "parada");
    const cartao = document.createElement("div");
    cartao.className = "sessao externa " + (encerrada ? "parada" : s.estado);
    cartao.dataset.sessao = s.sessao;

    const perfil = perfis.find((p) => p.id === contaDaSessao(s));
    const pasta = nomeDaPasta(s.pasta) || "sem pasta";

    const topo = document.createElement("div");
    topo.className = "sessao-topo";
    const pulso = document.createElement("span");
    pulso.className = "sessao-pulso";
    if (perfil && s.estado !== "parada" && !encerrada) pulso.style.background = perfil.cor;
    const ferramenta = perfil ? perfil.ferramentaNome : "Claude Code";
    const nome = document.createElement("span");
    nome.className = "sessao-pasta";
    nome.textContent = s.titulo || ferramenta;
    nome.title = s.titulo || s.pasta;
    const idade = document.createElement("span");
    idade.className = "sessao-idade";
    idade.textContent = tempoCurto(s.idade);
    topo.append(pulso, nome, idade);

    const linha = document.createElement("div");
    linha.className = "sessao-linha";
    if (encerrada) {
      linha.textContent = ENCERRADA.texto;
    } else if (s.estado === "rodando" && s.ferramenta) {
      const f = document.createElement("span");
      f.className = "ferramenta";
      f.textContent = s.ferramenta;
      const alvo = document.createElement("span");
      alvo.textContent = s.alvo || "";
      linha.append(f, alvo);
    } else {
      linha.textContent = (ESTADO[s.estado] || {}).texto || s.estado;
    }
    linha.title = encerrada ? ENCERRADA.dica : (ESTADO[s.estado] || {}).dica || "";

    const rodape = document.createElement("div");
    rodape.className = "sessao-rodape";
    const conta = document.createElement("span");
    conta.className = "sessao-conta";
    if (perfil) {
      conta.textContent = perfil.nome;
      conta.style.color = perfil.cor;
    } else {
      conta.textContent = "Principal";
      conta.title = "A conta que já estava logada antes deste app, fora dos perfis do Loom";
    }
    rodape.appendChild(conta);
    if (typeof s.contexto === "number") rodape.appendChild(barraContexto(s.contexto));

    const origem = document.createElement("div");
    origem.className = "sessao-origem";
    cartao.append(topo, origem, linha, rodape);
    pintarOrigem(cartao, ferramenta, s);
    cartao.addEventListener("contextmenu", (ev) => {
      ev.preventDefault();
      abrirMenuSessao(s, ev.clientX, ev.clientY);
    });
    // o cartao clareava no hover e o clique nao fazia nada: agora abre o mesmo menu
    cartao.addEventListener("click", (ev) => {
      ev.stopPropagation();
      abrirMenuSessao(s, ev.clientX, ev.clientY);
    });
    return cartao;
  }
}

function abasDaConta(conta) {
  return ordem.filter((a) => !a.ehPainel && a.viva && a.perfil && a.perfil.id === conta);
}

function abaDaSessao(s) {
  // depois do pareamento, quem manda e o dono registrado no cartao
  for (const aba of ordem) {
    if (!aba.ehPainel && aba.sessao && aba.sessao.sessao === s.sessao) return aba;
  }
  if (s.aba) {
    const exata = ordem.find((a) => a.sessionId === s.aba);
    if (exata) return exata;
  }
  return null;
}

function ehDaCasa(s) {
  // ser nossa quer dizer que da pra te levar ate ela. Sessao cuja aba ja foi fechada
  // vira acompanhamento, igual as de outro terminal.
  return !!abaDaSessao(s);
}

function irParaSessao(s) {
  const alvo = abaDaSessao(s);
  if (alvo) {
    ativar(alvo);
    return;
  }
  const conta = contaDaSessao(s);
  const abertas = conta && conta !== "__padrao__" ? abasDaConta(conta).length : 0;
  piscarAviso(
    abertas > 1
      ? "A conta " + ((perfis.find((p) => p.id === conta) || {}).nome || "") + " tem mais de uma aba aberta, não dá pra saber qual é"
      : "Essa sessão não tem aba aberta aqui, é só acompanhamento"
  );
}

let avisoRelogio = null;
function piscarAviso(texto) {
  let caixa = el("aviso");
  if (!caixa) {
    caixa = document.createElement("div");
    caixa.id = "aviso";
    caixa.className = "aviso";
    document.body.appendChild(caixa);
  }
  caixa.textContent = texto;
  caixa.classList.add("visivel");
  clearTimeout(avisoRelogio);
  avisoRelogio = setTimeout(() => caixa.classList.remove("visivel"), 2600);
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
  soEsta.hidden = !emGrade;
  grade.hidden = emGrade || ordem.filter((a) => !a.ehPainel).length < 2;
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
      navigator.clipboard.writeText(aba.term.getSelection());
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
    } else if (acao === "retomar-antes") {
      const conversa = aba.conversaAnterior;
      aba.conversaAnterior = "";
      anotarAbas();
      novaAba(aba.shell.id, aba.perfil ? aba.perfil.id : "", { pasta: pastaDaAba(aba), conversa });
    } else if (acao === "copiar") navigator.clipboard.writeText(pastaDaAba(aba));
    else if (acao === "so-esta") {
      ativar(aba);
      alternarGrade(false);
    } else if (acao === "grade") {
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

const elMenuSessao = el("menu-sessao");

function abrirMenuSessao(s, x, y) {
  sessaoDoMenu = s;
  // sem aba aqui, ir e encerrar ficavam sempre apagados, dois itens mortos no menu
  const daCasa = !!abaDaSessao(s);
  elMenuSessao.querySelector('[data-acao="ir"]').hidden = !daCasa;
  const encerrar = elMenuSessao.querySelector('[data-acao="encerrar"]');
  encerrar.hidden = !daCasa;
  encerrar.title = "fecha a aba desta sessão aqui no Loom";
  elMenuSessao.hidden = false;
  const caixa = elMenuSessao.getBoundingClientRect();
  elMenuSessao.style.left = Math.min(x, window.innerWidth - caixa.width - 10) + "px";
  elMenuSessao.style.top = Math.min(y, window.innerHeight - caixa.height - 10) + "px";
}

function fecharMenuSessao() {
  elMenuSessao.hidden = true;
  sessaoDoMenu = null;
}

elMenuSessao.querySelectorAll("[data-acao]").forEach((b) => {
  b.addEventListener("click", () => {
    const s = sessaoDoMenu;
    if (!s) return;
    const acao = b.dataset.acao;
    if (acao === "ir") irParaSessao(s);
    else if (acao === "atualizar") atualizarSessoes();
    else if (acao === "abrir") novaAbaEm(s);
    else if (acao === "retomar") retomarSessao(s);
    else if (acao === "copiar") navigator.clipboard.writeText(s.pasta || "");
    else if (acao === "encerrar") {
      const alvo = abaDaSessao(s);
      if (alvo) fecharComCuidado(alvo);
    }
    fecharMenuSessao();
  });
});

document.addEventListener("mousedown", (ev) => {
  if (!elMenuSessao.hidden && !elMenuSessao.contains(ev.target)) fecharMenuSessao();
});

function retomarSessao(s) {
  /* Abre uma aba na conta certa e manda o CLI continuar aquela conversa. */
  const conta = contaDaSessao(s);
  const perfil = perfis.find((p) => p.id === conta);
  if (s.estado === "pensando" || s.estado === "rodando") {
    piscarAviso("Essa conversa ainda está trabalhando em outro terminal");
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

function novaAbaEm(s) {
  const perfil = perfis.find((p) => p.id === contaDaSessao(s));
  // a pasta vai no proprio ConPTY, entao o shell ja nasce la dentro
  novaAba("", perfil ? perfil.id : "", { pasta: s.pasta || "" });
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
    // o id vai junto: o clique no balao leva pra esta aba
    if (bridge) bridge.notificar(aba.nomeDoCartao(), "Terminou e está esperando você", aba.sessionId || "");
  }
  for (const id of [...estadoAnterior.keys()]) if (!vistas.has(id)) estadoAnterior.delete(id);
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

let assinaturaSessoes = "";

let pedidoPendente = false;

function atualizarSessoes() {
  if (!bridge) return;
  if (varrendo) {
    // aviso que chega no meio de uma varredura nao pode sumir, senao a mudanca de estado
    // so aparece no proximo relogio, ate 2,5 s depois
    pedidoPendente = true;
    return;
  }
  varrendo = true;
  bridge.getSessions((json) => {
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
    const chave =
      json.replace(/"idade":\s*\d+/g, '"idade":0') +
      "|" +
      ordem
        .filter((a) => !a.ehPainel && a.sessao && TRABALHO.has(a.sessao.bruto) && teveSaidaRecente(a))
        .map((a) => a.sessionId)
        .join(",");
    if (chave !== assinaturaSessoes) {
      assinaturaSessoes = chave;
      montarSessoes();
    } else {
      envelhecer();
    }
    reagendarSessoes();
    if (pedidoPendente) {
      pedidoPendente = false;
      setTimeout(atualizarSessoes, 60);
    }
  });
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
  for (const cartao of elListaExternas.querySelectorAll("[data-sessao]")) {
    const s = porId.get(cartao.dataset.sessao);
    if (s) escrever(cartao, s);
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
    else if (dados.tipo === "imagem") aba.term.paste(comAspas(dados.valor));
    else if (dados.tipo === "arquivos") aba.term.paste(dados.valor.map(comAspas).join(" "));
  });
}

function comAspas(caminho) {
  return /[\s"']/.test(caminho) ? '"' + caminho.replace(/"/g, '\\"') + '"' : caminho;
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
          if (caminho) aba.term.paste(comAspas(caminho));
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

function soltarArquivos(caminhos) {
  /* Arrastar arquivo pra dentro escreve o caminho, que e o que o terminal entende.
     Caminho com espaco vai entre aspas, senao o shell parte no meio. */
  if (!abaAtiva || abaAtiva.ehPainel || !abaAtiva.sessionId) return;
  const texto = caminhos.map(comAspas).join(" ");
  if (!texto) return;
  abaAtiva.term.paste(texto + " ");
  abaAtiva.foco();
  piscarAviso(caminhos.length === 1 ? "Caminho colado" : caminhos.length + " caminhos colados");
}

function entregarColagem(aba, texto) {
  aba.term.paste(texto);
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
        sessao: a.sessionId || "",
        shell: a.shell.id,
        perfil: a.perfil ? a.perfil.id : "",
        pasta: a.sessao ? a.sessao.pasta || "" : (a.pedidoInicial || {}).pasta || "",
        // a conversa de antes vai junto enquanto nao houver outra, senao reabrir duas vezes
        // seguidas perdia o caminho de volta pra ela
        conversa: a.sessao ? a.sessao.sessao || "" : a.conversaAnterior || "",
        apelido: a.apelido || "",
        ativa: a === abaAtiva,
      }));
    // sem comparar, abas.json era reescrito em disco a cada ciclo de sessoes, o dia inteiro
    const texto = JSON.stringify(lista);
    if (texto === ultimaAnotacao) return;
    ultimaAnotacao = texto;
    bridge.saveTabs(texto);
  }, 600);
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
      // volta a aba, a conta e a pasta. A conversa NAO e retomada sozinha: isso dependia
      // de acertar o tempo de subida do CLI e falhava calado. Retomar e escolha sua, pelo
      // botao direito na aba, que guarda qual conversa rodava nela.
      const aba = novaAba(s.shell, s.perfil, { pasta: s.pasta });
      if (aba && s.conversa) aba.conversaAnterior = s.conversa;
      if (aba && s.apelido) {
        aba.apelido = s.apelido;
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
  estado.textContent = s ? (ESTADO[s.estado] || {}).texto || s.estado : "";

  /* So o essencial aqui: conta, conversa, estado e as duas reguas do plano.
     Modelo e contexto vivem na barra de cima e nao precisam brigar por espaco. */
  const partes = [];
  if (s || aba.perfil) {
    for (const [rotulo, chave] of [["5h", "five_hour"], ["7d", "seven_day"]]) {
      const faixa = faixaDaConta(aba, chave);
      if (!faixa || typeof faixa.usado !== "number") continue;
      const pct = Math.round(faixa.usado);
      const falta = faltaPara(faixa.zera);
      // so o percentual no chapeu: com o relogio junto ele empurrava o nome da conversa ate
      // sumir, e o nome e o que diz qual painel e qual
      partes.push(medida(rotulo, pct + "%", grau(pct), pct + "% usado" + (falta ? ", zera daqui a " + falta : "")));
    }
  }
  aba.chapeu.querySelector(".chapeu-medidas").innerHTML = partes.join("");
}

function pintarGrade() {
  const terminais = ordem.filter((a) => !a.ehPainel);
  const colunas = terminais.length <= 1 ? 1 : terminais.length <= 4 ? 2 : terminais.length <= 9 ? 3 : 4;
  elPalco.style.setProperty("--colunas", colunas);
  for (const aba of terminais) pintarChapeu(aba);
}

function ajustarTodas() {
  // cada terminal precisa remedir o proprio tamanho depois que o layout muda
  requestAnimationFrame(() => {
    for (const aba of ordem) if (!aba.ehPainel) aba.ajustar();
  });
}

function alternarGrade(ligar) {
  emGrade = typeof ligar === "boolean" ? ligar : !emGrade;
  elPalco.classList.toggle("grade", emGrade);
  document.querySelectorAll("#seletor-layout button").forEach((b) => {
    b.classList.toggle("ativo", (b.dataset.layout === "grade") === emGrade);
  });
  // na grade cada painel tem as proprias medidas, entao a barra de cima so repetiria
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
      if (emGrade) ajustarTodas();
      else if (abaAtiva) abaAtiva.ajustar();
    }, 300);
  });

  window.addEventListener("mouseup", () => {
    if (!ativo) return;
    ativo = false;
    puxador.classList.remove("ativo");
    document.body.classList.remove("puxando-largura");
    if (emGrade) ajustarTodas();
    else if (abaAtiva) abaAtiva.ajustar();
  });
})();

/* ------------------------------------------- arrastar a divisoria */

const elDivisor = el("divisor-fora");

function aplicarAlturaExternas() {
  const valor = Math.min(Math.max(cfg.alturaExternas || 25, 8), 75);
  elLateral.style.setProperty("--altura-externas", valor + "%");
}

(function ligarArraste() {
  let arrastando = false;
  let gravar = null;

  elDivisor.addEventListener("mousedown", (ev) => {
    ev.preventDefault();
    arrastando = true;
    elDivisor.classList.add("puxando");
    elLateral.classList.add("arrastando");
  });

  window.addEventListener("mousemove", (ev) => {
    if (!arrastando) return;
    const caixa = elLateral.getBoundingClientRect();
    const daBase = caixa.bottom - ev.clientY;
    const pct = Math.min(Math.max((daBase / caixa.height) * 100, 8), 75);
    cfg.alturaExternas = Math.round(pct);
    aplicarAlturaExternas();
    clearTimeout(gravar);
    gravar = setTimeout(() => {
      bridge.saveConfig(JSON.stringify({ alturaExternas: cfg.alturaExternas }), () => {});
    }, 400);
  });

  window.addEventListener("mouseup", () => {
    if (!arrastando) return;
    arrastando = false;
    elDivisor.classList.remove("puxando");
    elLateral.classList.remove("arrastando");
  });
})();

function alternarSessoesDeFora(mostrar) {
  cfg.mostrarSessoesDeFora = mostrar;
  bridge.saveConfig(JSON.stringify({ mostrarSessoesDeFora: mostrar }), (json) => {
    cfg = JSON.parse(json);
    montarSessoes();
  });
}

el("esconder-fora").addEventListener("click", (ev) => {
  ev.stopPropagation();
  alternarSessoesDeFora(false);
});
el("mostrar-fora").addEventListener("click", () => alternarSessoesDeFora(true));

el("atualizar-sessoes").addEventListener("click", atualizarSessoes);
function alternarLateral() {
  elLateral.classList.toggle("fechada");
  setTimeout(() => {
    if (emGrade) ajustarTodas();
    else if (abaAtiva) abaAtiva.ajustar();
  }, 200);
  if (!elLateral.classList.contains("fechada")) atualizarSessoes();
}

el("alternar-lateral").addEventListener("click", alternarLateral);

// alca grudada na divisa: fica sempre a vista, inclusive com a lateral ja fechada
const alcaLateral = el("recolher-lateral");
alcaLateral.addEventListener("click", (ev) => {
  ev.stopPropagation();
  alternarLateral();
});
// sem isso, clicar na alca comeca a arrastar a largura da lateral
alcaLateral.addEventListener("mousedown", (ev) => ev.stopPropagation());


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
  if (!bruto) return "";
  if (!bruto.startsWith("claude-")) return bruto;
  const limpo = bruto.replace(/^claude-/, "").replace(/-\d{8}$/, "").replace(/-latest$/, "");
  return limpo
    .split("-")
    .map((parte) => (/^\d/.test(parte) ? parte : parte.charAt(0).toUpperCase() + parte.slice(1)))
    .join(" ");
}

function leituraDaAba(aba) {
  return aba && !aba.ehPainel && aba.sessionId ? limitesPorAba[aba.sessionId] || null : null;
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
  caixa.querySelector("b").textContent = nome;
  caixa.querySelector("i").style.background = CORES_MODELO[familia] || "#5b6371";
  caixa.title = "Modelo que a sessão desta aba está usando";
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

function pintarUsoDaBarra() {
  const barra = el("uso-barra");
  // sem aba de terminal aberta nao ha conta em uso, e mostrar limite ali seria invencao
  const semAba = !abaAtiva || abaAtiva.ehPainel;
  const estava = barra.hidden;
  if (!cfg.mostrarUsoNaBarra || emGrade || semAba) {
    barra.hidden = true;
    if (!estava) refazerEncaixe();
    return;
  }
  barra.hidden = false;
  // a barra mora embaixo do palco, entao aparecer ou sumir muda a altura do terminal
  if (estava) refazerEncaixe();
  const chave = chaveDaAba();
  const perfil = perfis.find((p) => p.id === chave);

  const selo = el("uso-conta");
  selo.querySelector("b").textContent = perfil ? perfil.nome : "Principal";
  selo.querySelector("i").style.background = perfil ? perfil.cor : "#5b6371";
  selo.title = perfil
    ? "Tudo nesta barra é da conta desta aba"
    : "Pasta de login de sempre, usada pelas abas sem conta escolhida";

  // sem login nao existe limite pra mostrar, e emprestar o de outra conta so confunde
  const semLogin = !!perfil && !perfil.logado;
  const faixas = semLogin ? {} : (limitesPorPerfil[chave] || {}).faixas || {};
  // o limite chega pela status line do Claude: conta de outro CLI ficava em Sem leitura pra sempre
  const outroCli = !!perfil && !!perfil.ferramenta && perfil.ferramenta !== "claude";
  el("medidor-5h").hidden = outroCli;
  el("medidor-7d").hidden = outroCli;
  pintarMedidor("medidor-5h", faixas.five_hour, semLogin);
  pintarMedidor("medidor-7d", faixas.seven_day, semLogin);

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

/* ------------------------------------------------------------- atalhos */

window.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && !elBusca.hidden) {
    fecharBusca();
    return;
  }
  // caixa aberta vem antes da selecao: com uma selecao esquecida no terminal de tras, o
  // primeiro Esc so soltava a selecao e a caixa continuava ali
  if (ev.key === "Escape") {
    const aberto = veuAberto();
    if (aberto) return fecharVeu(aberto);
    if (!elMenu.hidden) return esconderMenu();
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
  } else if (ev.ctrlKey && !ev.shiftKey && ev.code === "KeyF") {
    ev.preventDefault();
    abrirBusca();
  } else if (ev.ctrlKey && ev.shiftKey && ev.code === "KeyG") {
    ev.preventDefault();
    alternarGrade();
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

function zoom(passo) {
  /* Ctrl com mais, menos e zero, e Ctrl com a roda. Mexe no tamanho da fonte do terminal,
     que e o que a pessoa quer dizer com zoom aqui. Zero volta ao padrao. */
  const atual = cfg.tamanhoFonte || FONTE_PADRAO;
  const novo = passo === 0 ? FONTE_PADRAO : Math.min(Math.max(atual + passo, 9), 22);
  if (novo === atual) return;
  cfg.tamanhoFonte = novo;
  aplicarTudo();
  piscarAviso("Fonte em " + novo + " px");
  bridge.saveConfig(JSON.stringify({ tamanhoFonte: novo }), (json) => {
    cfg = JSON.parse(json);
  });
}

let pendenteResize = null;
window.addEventListener("resize", () => {
  clearTimeout(pendenteResize);
  pendenteResize = setTimeout(() => {
    if (emGrade) ajustarTodas();
    else if (abaAtiva) abaAtiva.ajustar();
  }, 50);
});

/* ----------------------------------------------------------------- fps */

let quadros = 0;
let marco = performance.now();
let medindo = false;

function contar(agora) {
  if (!medindo) return;
  quadros++;
  if (agora - marco >= 1000) {
    const v = Math.round((quadros * 1000) / (agora - marco));
    el("fps").textContent = v + " fps";
    const alvo = el("sobre-fps");
    if (alvo) alvo.textContent = v + " por segundo";
    quadros = 0;
    marco = agora;
  }
  requestAnimationFrame(contar);
}

function medirFps(ligar) {
  // o proprio medidor mantinha o app desenhando sem parar. Desligado, ele nao custa nada.
  if (ligar === medindo) return;
  medindo = ligar;
  if (ligar) {
    quadros = 0;
    marco = performance.now();
    requestAnimationFrame(contar);
  } else {
    const alvo = el("sobre-fps");
    if (alvo) alvo.textContent = "Desligado";
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

  bridge.mexeram.connect(() => {
    atualizarSessoes();
    atualizarLimites();
  });

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

  bridge.sobre((json) => {
    try {
      const sobre = JSON.parse(json);
      el("sobre-versao").textContent = sobre.versao || "";
      el("sobre-raiz").textContent = sobre.raiz || "";
    } catch (e) {
      console.warn("sobre ilegivel", e);
    }
  });

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

  bridge.getHome((casa) => {
    pastaPessoal = (casa || "").replace(/[\\/]+$/, "");
  });

  bridge.getConfig((jsonCfg) => {
    cfg = JSON.parse(jsonCfg);
    bridge.elevacao((jsonElev) => {
      try {
        elevacao = JSON.parse(jsonElev);
      } catch (e) {
        elevacao = { disponivel: false, motivo: "sem sudo" };
      }
    });

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
          aplicarAlturaExternas();
          aplicarLarguraLateral();
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
