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
// A busca e Ctrl+Shift+F, como no Windows Terminal: com Ctrl+F ela tomava a tecla do programa da
// aba, e no less, no vim e no Git Bash o Ctrl+F nunca chegava
const ATALHOS_DA_JANELA = new Set(["KeyT", "KeyW", "KeyG", "KeyF", "KeyU"]);
const ATALHOS_DE_CONTROLE = new Set([
  "Comma", "Tab", "PageUp", "PageDown",
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
      if (emGrade) pintarChapeu(this);
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
      return tituloDaConversa(this.sessao, dona ? dona.ferramentaNome : "Claude Code");
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
    const ferramenta = dona ? dona.ferramentaNome : "Claude Code";
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
    if (mudou) this.fit.fit();
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
     Larga antes de ligar, pra conta de contextos nunca passar do teto nem por um instante. */
  const aVista = ordem.filter((a) => !a.ehPainel && (a === abaAtiva || emGrade));
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
  const saindo = abaAtiva;
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

function abrirStats(conta) {
  // aberto pelo medidor, vem na conta da aba: em Todas o bloco do limite nem aparecia, e quem
  // clicava no 5h da conta Trabalho caia numa tela sem 5h
  if (conta) escolherConta(conta);
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

// com uma aba de terminal na frente, abre na conta dela, que e onde o limite aparece
el("abrir-stats").addEventListener("click", () => abrirStats(abaAtiva && !abaAtiva.ehPainel ? chaveDaAba() : ""));
// clicar em 5h, 7d ou no nome da conta abre a tela de Uso na conta da aba. O contexto e da
// conversa e a tela de Uso nao fala dele, entao o clique nele so mostra o detalhe
el("uso-barra").addEventListener("click", (ev) => {
  const ctx = ev.target.closest("#medidor-ctx");
  if (ctx) {
    if (ctx.title) piscarAviso(ctx.title);
    return;
  }
  if (!ev.target.closest(".medidor, .uso-conta, .modelo")) return;
  const chave = chaveDaAba();
  // a Principal sem leitura leva a onde ela se liga, e nao a uma tela sem limite
  if (ev.target.closest(".medidor") && principalSemLeitura(perfis.find((p) => p.id === chave))) {
    abrirConfig("contas");
    return;
  }
  abrirStats(chave);
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
  return ["veu-escolha", "veu-conta", "veu-remover", "veu-config", "veu-fechar"].find((id) => !el(id).hidden);
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
  return !elMenu.hidden || !elMenuAba.hidden || !elMenuSessao.hidden;
}

function fecharMenus() {
  esconderMenu();
  elMenuAba.hidden = true;
  abaDoMenu = null;
  fecharMenuSessao();
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
    b.innerHTML = '<span class="ponto" style="background:#2f3540"></span><span></span>';
    b.querySelectorAll("span")[1].textContent = s.nome;
    if (padrao) {
      // o selo diz em qual conta a aba vai abrir, pra ninguem gastar o plano errado sem ver
      const selo = document.createElement("span");
      selo.className = "selo";
      selo.textContent = padrao.nome;
      selo.style.color = padrao.cor;
      selo.title = "Conta padrão, em Configurações, Terminal";
      b.appendChild(selo);
    }
    b.addEventListener("click", () => {
      esconderMenu();
      novaAba(s.id, padrao ? padrao.id : "");
    });

    const escudo = document.createElement("button");
    escudo.className = "menu-escudo";
    escudo.textContent = "adm";
    escudo.title = elevacao.disponivel
      ? "Abrir este shell com privilégio de administrador, sem conta"
      : TEXTO_DA_ELEVACAO[elevacao.motivo] ||
        "Precisa do sudo do Windows 11 ou do gsudo pra abrir elevado na mesma aba";
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
    cor.title = "trocar a cor";
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
  ["painelUso", "check"],
  ["larguraPainelUso", "range"],
  ["mostrarFps", "check"],
  ["desenho", "select"],
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
  ["avisoDeLimite", "check"],
  ["fecharAoSair", "check"],
  ["editor", "text"],
];

function montarSelecaoPerfis() {
  const sel = el("cfg-perfilPadrao");
  // "Nenhuma, shell puro" enganava: a aba sem conta abre o Claude da conta ja logada
  const deSempre = contaPadraoCadastrada();
  sel.innerHTML = "";
  const nenhuma = document.createElement("option");
  nenhuma.value = "";
  nenhuma.textContent = deSempre ? "Sem conta, abre na " + deSempre.nome : "Principal, o login do ~/.claude";
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
  PainelUso.aplicar();
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
      // a tela pode ter caido depois que a pagina subiu, e ai a proxima subida ja e outra
      bridge.desenho(receberDesenho);
      if (secao) mostrarSecao(secao);
      abrirVeu("veu-config");
      medirFps(true);
      // a varredura do historico so com a tela de Uso a vista: o Ctrl+virgula pra mexer na
      // fonte disparava a leitura de todas as contas e segurava os terminais enquanto rodava
      if (usoAVista()) pintarUso();
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

function tituloDaConversa(s, ferramenta) {
  /* Conversa ainda sem titulo lia "Claude Code" em cima e "Claude Code · pasta" embaixo, e tres
     delas viravam tres cartoes iguais. Sem titulo, o nome da pasta diz qual e qual. */
  return s.titulo || nomeDaPasta(s.pasta) || ferramenta;
}

function pintarOrigem(cartao, ferramenta, s, titulo) {
  /* Abaixo do titulo, quem gerou a conversa e onde ela roda. Quando o titulo ja e a pasta, so
     a ferramenta, pra nao repetir. */
  const alvo = cartao.querySelector(".sessao-origem");
  if (!alvo) return;
  alvo.innerHTML = "<b></b><i></i>";
  alvo.querySelector("b").textContent = ferramenta;
  const pasta = nomeDaPasta(s.pasta);
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
  // o que rodou numa aba que continua aberta: some da lista de fora e vira o Retomar dela
  const daAbaViva = new Set();

  for (const aba of terminais) {
    if (!aba.viva) continue;
    // Saindo do Claude com /exit ou Ctrl+C o shell fica, e a aba seguia com o titulo, o ready
    // e o contexto da conversa encerrada. Sem programa rodando no shell, nao ha Claude nela.
    // A elevada fica como era: o que roda nela esta fora do alcance da consulta.
    aba.programaVivo = !abasComPrograma || aba.elevado || abasComPrograma.has(aba.sessionId);
    const daqui = sessoes.filter((s) => !usadas.has(s.sessao) && s.aba && s.aba === aba.sessionId);
    if (!daqui.length) continue;
    for (const s of daqui) daAbaViva.add(s.sessao);
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

  // A conversa que nasceu aqui e cuja aba nao existe mais sumia da lista e virava so um numero
  // no rotulo. Ela entra com as de fora, depois delas, marcada como encerrada, e o botao
  // direito retoma. A que ainda trabalha provavelmente foi retomada em outro terminal. A de
  // uma aba que continua aberta, depois de um /clear ou de sair do Claude, nao entra: ela
  // aparecia como closed, com a aba viva, e o Encerrar dela fechava a aba da conversa atual.
  const soltas = sessoes.filter((s) => !usadas.has(s.sessao) && !daAbaViva.has(s.sessao));
  const deFora = soltas.filter((s) => s.terminal !== "loom");
  const orfas = soltas.filter((s) => s.terminal === "loom");
  const lista = deFora.concat(orfas);

  const rotulo = el("divisor-rotulo");
  const nomeDaLista = deFora.length ? (orfas.length ? "Fora das abas" : "Terminais externos") : "Conversas encerradas";
  if (rotulo) {
    rotulo.textContent = nomeDaLista;
    rotulo.title = orfas.length
      ? orfas.length === 1
        ? "Inclui 1 conversa deste app cuja aba já foi fechada"
        : "Inclui " + orfas.length + " conversas deste app cujas abas já foram fechadas"
      : "";
  }
  const nomeDoBotao = el("mostrar-fora-rotulo");
  if (nomeDoBotao) nomeDoBotao.textContent = nomeDaLista;
  elListaExternas.innerHTML = "";
  const mostrar = cfg.mostrarSessoesDeFora === true;
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
  // o contexto e o modelo da barra saem da aba pareada: saindo do Claude, eles saem junto
  pintarUsoDaBarra();
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
    // o ponto e do estado, como no cartao da aba; a conta ja esta no rodape
    if (perfil) pulso.style.setProperty("--cor-conta", perfil.cor);
    const ferramenta = perfil ? perfil.ferramentaNome : "Claude Code";
    const nome = document.createElement("span");
    nome.className = "sessao-pasta";
    nome.textContent = tituloDaConversa(s, ferramenta);
    nome.title = s.titulo || s.pasta;
    const idade = document.createElement("span");
    idade.className = "sessao-idade";
    idade.textContent = tempoCurto(s.idade);
    topo.append(pulso, nome, idade);

    const linha = document.createElement("div");
    linha.className = "sessao-linha";
    pintarLinhaDeEstado(linha, s, encerrada);
    // o clique abre um menu, e nao retoma direto como o texto antigo dizia
    cartao.title = encerrada ? "Fechada aqui. Clique pra ver as opções" : "Clique pra ver as opções";

    const rodape = document.createElement("div");
    rodape.className = "sessao-rodape";
    const conta = document.createElement("span");
    conta.className = "sessao-conta";
    if (perfil) {
      conta.textContent = perfil.nome;
      conta.style.color = perfil.cor;
    } else {
      conta.textContent = "Principal";
      conta.title = "Principal, o login do ~/.claude";
    }
    rodape.appendChild(conta);
    if (typeof s.contexto === "number") rodape.appendChild(barraContexto(s.contexto));

    const origem = document.createElement("div");
    origem.className = "sessao-origem";
    cartao.append(topo, origem, linha, rodape);
    pintarOrigem(cartao, ferramenta, s, nome.textContent);
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
  /* So o pareamento vale. Pela marca da aba, uma conversa antiga dela, de antes de um /clear
     ou de sair do Claude, devolvia a aba viva: o Encerrar fechava a conversa atual, e a barra
     podia mostrar o modelo e o contexto da velha. */
  for (const aba of ordem) {
    if (!aba.ehPainel && aba.sessao && aba.sessao.sessao === s.sessao) return aba;
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
    if (emGrade) pintarChapeu(aba);
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
    else if (acao === "copiar") copiarComAviso(s.pasta || "", "Caminho copiado: " + (s.pasta || ""));
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

let assinaturaSessoes = "";

let pedidoPendente = false;

// ids das abas com programa rodando no shell; null enquanto nao se sabe, e ai nada muda
let abasComPrograma = null;

function atualizarSessoes() {
  if (!bridge) return;
  if (varrendo) {
    // aviso que chega no meio de uma varredura nao pode sumir, senao a mudanca de estado
    // so aparece no proximo relogio, ate 2,5 s depois
    pedidoPendente = true;
    return;
  }
  varrendo = true;
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
  const texto = caminhos.map(comAspas).join(" ");
  if (!texto) return;
  if (aba !== abaAtiva) ativar(aba);
  aba.term.paste(texto + " ");
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
        sessao: a.sessionId || "",
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
      // botao direito na aba, que guarda qual conversa rodava nela. E a aba que tinha conversa
      // volta parada no prompt: com o CLI digitado sozinho nascia um Claude em branco em cada
      // uma, e a conversa de antes so voltava abrindo outra aba.
      const aba = novaAba(s.shell, s.perfil, { pasta: s.pasta, semCli: !!s.conversa });
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
    if (emGrade) reencaixarGrade();
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
  const ponto = document.querySelector("#abrir-stats .pilula-ponto");
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
          aplicarAlturaExternas();
          aplicarLarguraLateral();
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
