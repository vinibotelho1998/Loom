"use strict";


const TEMA = {
  background: "#000000",
  foreground: "#d3d8e0",
  cursor: "#4f9cf9",
  cursorAccent: "#000000",
  selectionBackground: "#22344a",
  black: "#000000", red: "#e05561", green: "#6cc98a", yellow: "#d5b06b",
  blue: "#4f9cf9", magenta: "#b088f0", cyan: "#56c2c9", white: "#c8cfda",
  brightBlack: "#5b6371", brightRed: "#f0808a", brightGreen: "#8ce0a8",
  brightYellow: "#f0cd85", brightBlue: "#7fb8ff", brightMagenta: "#cba8ff",
  brightCyan: "#87dce2", brightWhite: "#eef2f8",
};

const FONTE = '"JetBrains Mono", Consolas, monospace';

const el = (id) => document.getElementById(id);
const elAbas = el("abas");
const elPalco = el("palco");
const elMenu = el("menu");

let bridge = null;
let shells = [];
let perfis = [];
let ferramentas = [];
let cfg = {};
const porSessao = new Map();
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

/* ------------------------------------------------------ aba estatisticas */

class AbaStats extends AbaBase {
  constructor() {
    super("Uso");
    this.botao.querySelector(".ponto").style.background = "#3f7fd0";
    this.tela.classList.add("tela-painel");
    this.tela.innerHTML = '<div class="stats"><div class="vazio">Lendo os transcript...</div></div>';
    this.recarregar();
    this.relogio = setInterval(() => {
      if (abaAtiva === this) this.recarregar();
    }, 30000);
  }

  recarregar() {
    bridge.getUsage((json) => {
      let dados = {};
      try {
        dados = JSON.parse(json);
      } catch (e) {
        console.warn("uso ilegivel", e);
      }
      this.tela.innerHTML = renderStats(dados, perfis);
      pintarGrafico();
      ligarFiltroGrafico();
    });
  }

  foco() {
    this.recarregar();
  }

  destruir() {
    clearInterval(this.relogio);
    super.destruir();
  }
}

/* --------------------------------------------------------- aba terminal */

function opcoesTerminal() {
  return {
    theme: TEMA,
    fontFamily: FONTE,
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
  constructor(shell, perfil) {
    super(shell.nome);
    this.ehPainel = false;
    this.shell = shell;
    this.perfil = perfil || null;
    this.pendentes = [];


    this.term = new Terminal(opcoesTerminal());
    this.fit = new FitAddon.FitAddon();
    this.term.loadAddon(this.fit);
    this.busca = new SearchAddon.SearchAddon();
    this.term.loadAddon(this.busca);
    this.term.loadAddon(new WebLinksAddon.WebLinksAddon());
    this.chapeu = document.createElement("div");
    this.chapeu.className = "chapeu";
    this.chapeu.innerHTML =
      '<span class="chapeu-ponto"></span>' +
      '<span class="chapeu-nome"></span>' +
      '<span class="chapeu-estado"></span>' +
      '<span class="chapeu-medidas"></span>';
    this.tela.appendChild(this.chapeu);

    this.term.open(this.tela);
    this.tela.addEventListener(
      "paste",
      (ev) => {
        const limite = cfg.avisarColagemLinhas || 0;
        const texto = ev.clipboardData ? ev.clipboardData.getData("text") : "";
        if (!limite || !texto || contarLinhas(texto) <= limite) return;
        ev.preventDefault();
        ev.stopPropagation();
        pedirColagem(texto, this);
      },
      true
    );
    this.tela.addEventListener("mousedown", () => {
      if (emGrade && abaAtiva !== this) ativar(this);
    });
    // na grade o botao direito abre o menu da aba; fora dela o xterm continua dono do clique
    this.tela.addEventListener(
      "contextmenu",
      (ev) => {
        if (!emGrade) return;
        ev.preventDefault();
        ev.stopPropagation();
        ativar(this);
        abrirMenuAba(this, ev.clientX, ev.clientY);
      },
      true
    );
    try {
      this.term.loadAddon(new WebglAddon.WebglAddon());
    } catch (e) {
      console.warn("sem webgl, caiu no canvas", e);
    }

    this.pintar();

    this.term.onData((d) => {
      if (bridge && this.sessionId) bridge.write(this.sessionId, d);
      // apertou Enter: mandou algo, entao a lista tem que reagir junto
      if (d.charCodeAt(0) === 13 || d.charCodeAt(0) === 10) setTimeout(atualizarSessoes, 250);
    });
    this.term.onResize((dim) => {
      if (bridge && this.sessionId) bridge.resize(this.sessionId, dim.cols, dim.rows);
    });
    this.term.attachCustomKeyEventHandler((ev) => {
      if (ev.type !== "keydown") return true;
      if (ev.ctrlKey && ev.shiftKey && (ev.code === "KeyT" || ev.code === "KeyW")) return false;
      if (ev.ctrlKey && (ev.code === "Comma" || ev.code === "Tab" || ev.code === "KeyF")) return false;
      return true;
    });
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
      titulo.textContent = this.shell.nome;
      titulo.title = "";
      const origem = this.botao.querySelector(".sessao-origem");
      if (origem) origem.textContent = "";
      this.botao.querySelector(".sessao-linha").textContent = this.viva ? "shell" : "closed";
      this.botao.querySelector(".sessao-idade").textContent = "";
    }
  }

  refletir(s) {
    /* Traz pro cartao da aba o estado da sessao de Claude que roda dentro dela. */
    this.sessao = s || null;
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
    const titulo = cartao.querySelector(".rotulo");
    titulo.textContent = s.titulo || ferramenta;
    titulo.title = s.titulo || "";
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
    ativar(this);
    // a tela acabou de nascer: medir agora daria tamanho errado e o CLI desenharia torto
    setTimeout(() => {
      this.fit.fit();
      const perfilId = this.perfil ? this.perfil.id : "";
      bridge.openSessionWith(
        this.shell.id,
        perfilId,
        this.term.cols,
        this.term.rows,
        JSON.stringify(pedido || {}),
        (id) => {
          this.sessionId = id;
          porSessao.set(id, this);
          for (const data of this.pendentes.splice(0)) this.term.write(data);
          if (abaAtiva === this) this.term.focus();
          // o CLI leva um instante pra desenhar; remedir aqui evita quebra de layout
          setTimeout(() => this.ajustar(), 400);
        }
      );
    }, 0);
  }

  escrever(data) {
    if (this.sessionId) this.term.write(data);
    else this.pendentes.push(data);
  }

  encerrada(code) {
    this.viva = false;
    this.pintar();
    this.term.write("\r\n\x1b[38;5;240m[sessao encerrada, codigo " + code + "]\x1b[0m\r\n");
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
    const o = opcoesTerminal();
    for (const chave of ["fontSize", "lineHeight", "letterSpacing", "cursorStyle", "cursorBlink", "scrollback"]) {
      this.term.options[chave] = o[chave];
    }
    this.fit.fit();
  }

  destruir() {
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
  if (abaAtiva) {
    abaAtiva.tela.classList.remove("ativa");
    abaAtiva.botao.classList.remove("ativa");
  }
  abaAtiva = aba;
  aba.tela.classList.add("ativa");
  aba.botao.classList.add("ativa");
  pintarUsoDaBarra();
  atualizarTituloDaJanela();
  requestAnimationFrame(() => {
    aba.ajustar();
    aba.foco();
  });
}

function fechar(aba) {
  const i = ordem.indexOf(aba);
  const eraAtiva = abaAtiva === aba;
  aba.destruir();
  if (eraAtiva) {
    abaAtiva = null;
    const alvo = ordem[Math.min(i, ordem.length - 1)];
    if (alvo) ativar(alvo);
  }
  montarSessoes();
  anotarAbas();
  if (emGrade) {
    pintarGrade();
    ajustarTodas();
  }
}

function novaAba(shellId, perfilId, pedido) {
  const shell = shells.find((s) => s.id === shellId) || shells[0];
  const perfil = perfis.find((p) => p.id === perfilId) || null;
  if (!shell) return;
  const aba = new Aba(shell, perfil);
  aba.abrir(pedido);
  montarSessoes();
  if (emGrade) {
    pintarGrade();
    ajustarTodas();
  }
  setTimeout(atualizarSessoes, 300);
  return aba;
}

function abaPadrao() {
  novaAba(cfg.shellPadrao || (shells.length ? shells[0].id : ""), cfg.perfilPadrao || "");
}

function abrirStats() {
  const existente = ordem.find((a) => a instanceof AbaStats);
  if (existente) {
    ativar(existente);
    existente.recarregar();
    return;
  }
  ativar(new AbaStats());
}

el("abrir-stats").addEventListener("click", abrirStats);
document.querySelectorAll(".uso-item").forEach((b) => b.addEventListener("click", abrirStats));

/* ------------------------------------------------------------------ veu */

function abrirVeu(id) {
  el(id).hidden = false;
}

function fecharVeu(id) {
  el(id).hidden = true;
  if (id === "veu-config") medirFps(!!cfg.mostrarFps);
  if (abaAtiva) abaAtiva.foco();
}

function veuAberto() {
  return ["veu-conta", "veu-remover", "veu-config", "veu-colar", "veu-fechar"].find((id) => !el(id).hidden);
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
    alvoShells.appendChild(b);
  }

  const alvoPerfis = el("menu-perfis");
  alvoPerfis.innerHTML = "";
  if (!perfis.length) {
    const vazio = document.createElement("div");
    vazio.className = "menu-item";
    vazio.style.color = "#3c424d";
    vazio.textContent = "Nenhuma conta cadastrada";
    alvoPerfis.appendChild(vazio);
  }
  for (const p of perfis) {
    const b = document.createElement("button");
    b.className = "menu-item";
    b.innerHTML = '<span class="ponto"></span><span class="nome"></span><span class="selo"></span>';
    b.querySelector(".ponto").style.background = p.cor;
    b.querySelector(".nome").textContent = p.nome;
    b.title = (p.email || p.ferramentaNome) + " · " + p.configDir;
    const selo = b.querySelector(".selo");
    selo.textContent = p.logado ? "Logado" : "Sem login";
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
  el("dica-ferramenta").textContent = f
    ? f.env + " aponta a pasta · login em " + f.login
    : "";
}

el("ferramenta-conta").addEventListener("change", atualizarDicaFerramenta);

function pedirNovaConta() {
  esconderMenu();
  montarSelecaoFerramentas();
  abrirVeu("veu-conta");
  el("nome-conta").value = "";
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
  const nome = el("nome-conta").value.trim();
  if (!nome) return;
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

/* ------------------------------------------------------------- remocao */

function pedirRemocao(perfil) {
  removendo = perfil;
  el("remover-nome").textContent = perfil.nome;
  el("remover-texto").textContent = perfil.emprestada
    ? "Essa conta aponta pra pasta de login que já existia antes do app. Remover tira ela só do cadastro daqui; o login continua onde sempre esteve."
    : perfil.logado
    ? "Essa conta esta logada nesta maquina. Sem marcar a opcao abaixo, o login continua no disco e voce pode recadastrar depois sem precisar entrar de novo."
    : "Essa conta ainda nao tem login nesta maquina.";
  el("remover-login").checked = false;
  el("remover-login").closest(".marca-caixa").hidden = !!perfil.emprestada;
  abrirVeu("veu-remover");
}

el("confirmar-remocao").addEventListener("click", () => {
  if (!removendo) return;
  bridge.removeProfile(removendo.id, el("remover-login").checked);
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
    info.textContent = p.emprestada ? "Login de sempre" : p.logado ? "Logado · " + tamanho(p.bytes) : "Sem login";
    info.title = p.configDir;

    let entrar = null;
    if (!p.logado && !p.emprestada) {
      entrar = document.createElement("button");
      entrar.className = "conta-acao entrar";
      entrar.textContent = "Entrar";
      entrar.title = "Abre uma aba nesta conta e já roda o /login";
      entrar.addEventListener("click", () => {
        fecharVeu("veu-config");
        novaAba("", p.id, { entrar: true });
        piscarAviso("Siga o login que abriu na aba");
      });
    }

    const esquecer = document.createElement("button");
    esquecer.className = "conta-acao";
    esquecer.textContent = "Deslogar";
    esquecer.style.opacity = p.logado && !p.emprestada ? "1" : ".4";
    esquecer.title = "apaga so a credencial, mantendo historico e ajustes";
    esquecer.addEventListener("click", () => {
      if (!p.logado || p.emprestada) {
        if (p.emprestada) piscarAviso("Essa conta usa a pasta de login que já existia, o app não mexe nela");
        return;
      }
      bridge.forgetLogin(p.id, () => recarregarPerfis(montarContas));
    });

    const apagar = document.createElement("button");
    apagar.className = "conta-acao perigo";
    apagar.textContent = "Remover";
    apagar.addEventListener("click", () => pedirRemocao(p));

    linha.append(cor, nome, cli, info);
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
    linha.appendChild(titulo);

    for (const [chave, rotulo] of [["comando", "comando"], ["env", "variavel de ambiente"], ["login", "arquivo de login"]]) {
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
  ["tamanhoFonte", "range"],
  ["entrelinha", "range"],
  ["espacoLetra", "range"],
  ["cursor", "select"],
  ["cursorPisca", "check"],
  ["mostrarFps", "check"],
  ["shellPadrao", "select"],
  ["perfilPadrao", "select"],
  ["historico", "number"],
  ["avisarColagemLinhas", "number"],
  ["confirmarFechar", "check"],
  ["abrirCliDaConta", "check"],
  ["mostrarUsoNaBarra", "check"],
  ["confiarPastas", "check"],
  ["restaurarAbas", "check"],
  ["esconderStatusLine", "check"],
];

function montarSelecaoPerfis() {
  const sel = el("cfg-perfilPadrao");
  sel.innerHTML = '<option value="">nenhuma, shell puro</option>';
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
    const saida = el("out-" + chave);
    if (saida) saida.textContent = cfg[chave];
  }
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
  atualizarUsoDaBarra();
}

function ligarCampos() {
  for (const [chave, tipo] of CAMPOS) {
    const campo = el("cfg-" + chave);
    if (!campo) continue;
    campo.addEventListener(tipo === "range" ? "input" : "change", () => {
      let valor;
      if (tipo === "check") valor = campo.checked;
      else if (tipo === "number") valor = parseInt(campo.value, 10) || 0;
      else if (tipo === "range") valor = chave === "entrelinha" ? parseFloat(campo.value) : parseInt(campo.value, 10);
      else valor = campo.value;
      cfg[chave] = valor;
      bridge.saveConfig(JSON.stringify({ [chave]: valor }), (json) => {
        cfg = JSON.parse(json);
        aplicarTudo();

      });
    });
  }
}

document.querySelectorAll(".nav-item").forEach((b) => {
  b.addEventListener("click", () => {
    document.querySelectorAll(".nav-item").forEach((o) => o.classList.remove("ativa"));
    document.querySelectorAll(".secao").forEach((s) => s.classList.remove("ativa"));
    b.classList.add("ativa");
    el("sec-" + b.dataset.alvo).classList.add("ativa");
  });
});

function abrirConfig() {
  esconderMenu();
  carregarFerramentas(() => {
    recarregarPerfis(() => {
      montarContas();
      montarFerramentas();
      montarSelecaoPerfis();
      montarSelecaoShells();
      preencherCampos();
      abrirVeu("veu-config");
      medirFps(true);
    });
  });
}

el("abrir-config").addEventListener("click", abrirConfig);

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
  parada: { texto: "stopped", dica: "Sem nada escrito há bastante tempo" },
};

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
  ctx.title = "janela de contexto desta sessao";
  return ctx;
}

function montarSessoes() {
  const contador = el("conta-sessoes");
  const terminais = ordem.filter((a) => !a.ehPainel);
  if (contador) contador.textContent = terminais.length ? String(terminais.length) : "";

  // Pareamento em duas passadas. Primeiro a marca exata, que so existe enquanto a aba viveu.
  // Depois, por conta, porque reiniciar o app troca o id da aba e a marca antiga fica orfa.
  const usadas = new Set();
  const donos = new Map();

  for (const aba of terminais) {
    const exata = sessoes.find((s) => !usadas.has(s.sessao) && s.aba && s.aba === aba.sessionId);
    if (exata) {
      usadas.add(exata.sessao);
      donos.set(aba, exata);
    }
  }

  // so entra na segunda passada quem nasceu numa aba deste app: sessao de outro terminal
  // pode ser da mesma conta e seria adotada por engano
  const vivas = sessoes
    .filter((s) => !usadas.has(s.sessao) && s.estado !== "parada" && s.terminal === "dbx-term")
    .sort((a, b) => a.idade - b.idade);

  for (const aba of terminais) {
    if (donos.has(aba)) continue;
    const conta = aba.perfil ? aba.perfil.id : "__padrao__";
    const candidata = vivas.find((s) => !usadas.has(s.sessao) && contaDaSessao(s) === conta);
    if (candidata) {
      usadas.add(candidata.sessao);
      donos.set(aba, candidata);
    }
  }

  for (const aba of terminais) aba.refletir(donos.get(aba) || null);
  atualizarTituloDaJanela();
  anotarAbas();

  if (emGrade) pintarGrade();

  // sessao que nasceu aqui e cuja aba nao existe mais esta morta: o shell dela morreu junto
  // com o app. Nao e de outro terminal nem esta rodando, entao e historico e sai da lista.
  const deFora = sessoes.filter((s) => !usadas.has(s.sessao) && s.terminal !== "dbx-term");
  const orfas = sessoes.filter((s) => !usadas.has(s.sessao) && s.terminal === "dbx-term");

  const rotulo = el("divisor-rotulo");
  if (rotulo) {
    rotulo.textContent = orfas.length
      ? "Fora do DBX Term · " + orfas.length + " encerradas"
      : "Fora do DBX Term";
    rotulo.title = orfas.length
      ? orfas.length + " sessões deste app já encerradas ficaram no histórico e não aparecem aqui"
      : "";
  }
  elListaExternas.innerHTML = "";
  el("divisor-fora").hidden = !deFora.length;
  elListaExternas.hidden = !deFora.length;
  for (const s of deFora) elListaExternas.appendChild(montarCartao(s));
  elListaExternas.classList.toggle("encolhida", !deFora.length);
  aplicarAlturaExternas();
}

function montarCartao(s) {
  {
    const cartao = document.createElement("div");
    cartao.className = "sessao externa " + s.estado;

    const perfil = perfis.find((p) => p.id === contaDaSessao(s));
    const pasta = nomeDaPasta(s.pasta) || "sem pasta";

    const topo = document.createElement("div");
    topo.className = "sessao-topo";
    const pulso = document.createElement("span");
    pulso.className = "sessao-pulso";
    if (perfil && s.estado !== "parada") pulso.style.background = perfil.cor;
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

    const rodape = document.createElement("div");
    rodape.className = "sessao-rodape";
    const conta = document.createElement("span");
    conta.className = "sessao-conta";
    if (perfil) {
      conta.textContent = perfil.nome;
      conta.style.color = perfil.cor;
    } else {
      conta.textContent = "Principal";
      conta.title = "A conta que já estava logada antes deste app, fora dos perfis do DBX Term";
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
      ? "a conta " + ((perfis.find((p) => p.id === conta) || {}).nome || "") + " tem mais de uma aba aberta, nao da pra saber qual e"
      : "essa sessao nao tem aba aberta aqui, e so acompanhamento"
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

function abrirMenuAba(aba, x, y) {
  abaDoMenu = aba;
  const soEsta = elMenuAba.querySelector('[data-aba="so-esta"]');
  const grade = elMenuAba.querySelector('[data-aba="grade"]');
  soEsta.hidden = !emGrade;
  grade.hidden = emGrade || ordem.filter((a) => !a.ehPainel).length < 2;
  const copiar = elMenuAba.querySelector('[data-aba="copiar"]');
  copiar.disabled = !(aba.sessao && aba.sessao.pasta);
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
    else if (acao === "duplicar") novaAba(aba.shell.id, aba.perfil ? aba.perfil.id : "");
    else if (acao === "copiar" && aba.sessao) navigator.clipboard.writeText(aba.sessao.pasta || "");
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

const elMenuSessao = el("menu-sessao");

function abrirMenuSessao(s, x, y) {
  sessaoDoMenu = s;
  const daCasa = !!abaDaSessao(s);
  elMenuSessao.querySelector('[data-acao="ir"]').disabled = !daCasa;
  const encerrar = elMenuSessao.querySelector('[data-acao="encerrar"]');
  encerrar.disabled = !daCasa;
  encerrar.title = daCasa
    ? "fecha a aba desta sessao aqui no DBX Term"
    : "essa sessao nao e deste app, entao quem fecha e o terminal onde ela roda";
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
  const perfil = perfis.find((p) => p.id === contaDaSessao(s));
  if (!perfil) {
    piscarAviso("Essa sessão é de uma conta que não está cadastrada aqui");
    return;
  }
  novaAba("", perfil.id, { pasta: s.pasta, conversa: s.sessao });
}

function novaAbaEm(s) {
  const perfil = perfis.find((p) => p.id === contaDaSessao(s));
  novaAba("", perfil ? perfil.id : "");
  if (s.pasta) {
    const alvo = ordem[ordem.length - 1];
    setTimeout(() => {
      if (alvo && alvo.sessionId) bridge.write(alvo.sessionId, 'cd "' + s.pasta + '"' + String.fromCharCode(13));
    }, 900);
  }
}

let varrendo = false;
let relogioSessoes = null;

function atualizarSessoes() {
  if (!bridge || varrendo) return;
  varrendo = true;
  bridge.getSessions((json) => {
    varrendo = false;
    try {
      sessoes = JSON.parse(json);
    } catch (e) {
      sessoes = [];
    }
    montarSessoes();
    reagendarSessoes();
  });
}

function reagendarSessoes() {
  /* O aviso de arquivo mexido e quem manda; este relogio e so a rede de seguranca
     pra idade envelhecer na tela e pra pegar o que o vigia deixar passar. */
  const acesa = sessoes.some((s) => s.estado === "rodando" || s.estado === "pensando");
  clearTimeout(relogioSessoes);
  relogioSessoes = setTimeout(atualizarSessoes, acesa ? 1500 : 6000);
}

/* ------------------------------------------------------------- colar */

let colagemPendente = null;

const QUEBRA_DE_LINHA = new RegExp('\\r\\n|\\r|\\n', 'g');

function contarLinhas(texto) {
  return texto.split(QUEBRA_DE_LINHA).length;
}

function pedirColagem(texto, aba) {
  colagemPendente = { texto, aba };
  const partes = texto.split(QUEBRA_DE_LINHA);
  el("colar-linhas").textContent = partes.length + " linhas";
  const mostra = partes.slice(0, 8).join("\n");
  const resto = partes.length > 8 ? "\n… mais " + (partes.length - 8) + " linhas" : "";
  el("colar-previa").textContent = mostra + resto;
  abrirVeu("veu-colar");
}

el("confirmar-colagem").addEventListener("click", () => {
  const pedido = colagemPendente;
  colagemPendente = null;
  fecharVeu("veu-colar");
  if (pedido && pedido.aba) pedido.aba.term.paste(pedido.texto);
});

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

function buscar(paraTras) {
  if (!abaAtiva || abaAtiva.ehPainel || !abaAtiva.busca) return;
  const termo = elBuscaTermo.value;
  if (!termo) {
    abaAtiva.busca.clearDecorations();
    el("busca-conta").textContent = "";
    return;
  }
  const achou = paraTras
    ? abaAtiva.busca.findPrevious(termo, PINTURA)
    : abaAtiva.busca.findNext(termo, PINTURA);
  el("busca-conta").textContent = achou ? "" : "nada";
}

elBuscaTermo.addEventListener("input", () => buscar(false));
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
  fechamentoPendente = aba;
  el("fechar-nome").textContent = aba.sessao
    ? nomeDaTela(aba)
    : aba.shell.nome + (aba.perfil ? " · " + aba.perfil.nome : "");
  el("fechar-texto").textContent = aba.sessao
    ? "Tem uma sessão do Claude rodando nessa aba. Fechar encerra ela junto."
    : "O shell dessa aba ainda está aberto. Fechar encerra ele e o que estiver rodando dentro.";
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

function anotarAbas() {
  if (!bridge || !cfg.restaurarAbas) return;
  clearTimeout(gravarAbas);
  gravarAbas = setTimeout(() => {
    const lista = ordem
      .filter((a) => !a.ehPainel && a.viva)
      .map((a) => ({
        shell: a.shell.id,
        perfil: a.perfil ? a.perfil.id : "",
        pasta: a.sessao ? a.sessao.pasta || "" : "",
        conversa: a.sessao ? a.sessao.sessao || "" : "",
        ativa: a === abaAtiva,
      }));
    bridge.saveTabs(JSON.stringify(lista));
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
      // de acertar o tempo de subida do CLI e falhava calado. Retomar e escolha sua,
      // pelo botao direito na sessao.
      const aba = novaAba(s.shell, s.perfil, { pasta: s.pasta });
      if (aba && s.ativa) ativa = aba;
    }
    if (ativa) ativar(ativa);
    depois(true);
  });
}

/* ------------------------------------------------------------- grade */

let emGrade = false;

function nomeDaTela(aba) {
  const s = aba.sessao;
  if (s && s.pasta) {
    return (aba.perfil ? aba.perfil.nome + " · " : "") + nomeDaPasta(s.pasta);
  }
  return (aba.perfil ? aba.perfil.nome + " · " : "") + aba.shell.nome;
}

function medida(rotulo, valor, classe) {
  return '<b class="' + (classe || "") + '"><i>' + rotulo + "</i>" + valor + "</b>";
}

function faixaDaConta(aba, qual) {
  const chave = aba.perfil ? aba.perfil.id : "__padrao__";
  if (aba.perfil && !aba.perfil.logado) return null;
  const faixas = (limitesPorPerfil[chave] || {}).faixas || {};
  const f = faixas[qual];
  return f && typeof f.usado === "number" ? Math.round(f.usado) : null;
}

function grau(pct) {
  if (pct === null) return "";
  return pct >= 85 ? "critico" : pct >= 60 ? "atencao" : "";
}

function pintarChapeu(aba) {
  const cor = aba.perfil ? aba.perfil.cor : "#5b6371";
  aba.tela.style.setProperty("--cor-conta", cor);
  aba.chapeu.querySelector(".chapeu-ponto").style.background = cor;
  aba.chapeu.querySelector(".chapeu-nome").textContent = nomeDaTela(aba);

  const estado = aba.chapeu.querySelector(".chapeu-estado");
  const s = aba.sessao;
  const viva = leituraDaAba(aba);
  const nomeModelo = nomeBonito((viva && viva.modelo) || (s && s.modelo) || "");
  aba.chapeu.querySelector(".chapeu-nome").title = nomeModelo ? "Modelo: " + nomeModelo : "";
  estado.className = "chapeu-estado" + (s ? " " + s.estado : "");
  estado.textContent = s ? (ESTADO[s.estado] || {}).texto || s.estado : "";

  // shell puro, sem conta escolhida e sem Claude rodando, nao tem limite pra mostrar:
  // o numero seria o da conta principal e nao diria nada sobre aquela aba
  const mede = !!s || !!aba.perfil || !!viva;
  const partes = [];
  if (mede) {
    const cinco = faixaDaConta(aba, "five_hour");
    const sete = faixaDaConta(aba, "seven_day");
    const bruto = viva && typeof viva.contexto === "number" ? viva.contexto : s && s.contexto;
    const ctx = typeof bruto === "number" ? Math.round(bruto) : null;
    const hoje = ((usoPorPerfil[aba.perfil ? aba.perfil.id : "__padrao__"] || {}).hoje || {}).saida || 0;
    if (ctx !== null) {
      const detalhe =
        viva && typeof viva.contextoTokens === "number" && viva.contextoTeto
          ? " " + curtoBarra(viva.contextoTokens) + "/" + curtoBarra(viva.contextoTeto)
          : "";
      partes.push(medida("ctx", ctx + "%" + detalhe, grau(ctx)));
    }
    if (cinco !== null) partes.push(medida("5h", cinco + "%", grau(cinco)));
    if (sete !== null) partes.push(medida("7d", sete + "%", grau(sete)));
    if (hoje) partes.push(medida("hoje", curtoBarra(hoje), ""));
    if (nomeModelo) partes.unshift('<b class="chapeu-modelo">' + nomeModelo + "</b>");
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

el("atualizar-sessoes").addEventListener("click", atualizarSessoes);
el("alternar-lateral").addEventListener("click", () => {
  elLateral.classList.toggle("fechada");
  setTimeout(() => {
    if (emGrade) ajustarTodas();
    else if (abaAtiva) abaAtiva.ajustar();
  }, 200);
  if (!elLateral.classList.contains("fechada")) atualizarSessoes();
});


/* ------------------------------------------------------ limite do plano */

let usoPorPerfil = {};
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
    (typeof viva.contextoSobra === "number" ? "sobram " + Math.round(viva.contextoSobra) + "% ate compactar" : "");
}

let tituloNaJanela = "";

function atualizarTituloDaJanela() {
  const s = sessaoDaAba();
  const aba = abaAtiva && !abaAtiva.ehPainel ? abaAtiva : null;
  let texto = "";
  if (s && s.titulo) texto = s.titulo;
  else if (aba) texto = (aba.perfil ? aba.perfil.nome + " · " : "") + aba.shell.nome;
  else if (abaAtiva && abaAtiva.ehPainel) texto = "Uso";
  if (texto === tituloNaJanela) return;
  tituloNaJanela = texto;
  if (bridge) bridge.setTitle(texto);
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
  caixa.title = "Modelo que a sessao desta aba esta usando";
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
  const h = Math.floor(alvo / 3600000);
  const m = Math.round((alvo % 3600000) / 60000);
  return h >= 24 ? Math.floor(h / 24) + "d" + (h % 24) + "h" : h ? h + "h" + m + "m" : m + "m";
}

function pintarMedidor(id, faixa, semLogin) {
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
      ? "esta conta ainda nao fez login nesta maquina, entao nao existe limite pra ler. Rode /login na aba dela."
      : "Ainda sem leitura. O percentual do plano so chega junto com uma resposta do Claude, " +
        "entao ele aparece depois da primeira mensagem numa aba desta conta.";
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
  if (!cfg.mostrarUsoNaBarra || emGrade) {
    barra.hidden = true;
    return;
  }
  barra.hidden = false;
  const chave = chaveDaAba();
  const perfil = perfis.find((p) => p.id === chave);

  const selo = el("uso-conta");
  selo.querySelector("b").textContent = perfil ? perfil.nome : "Principal";
  selo.querySelector("i").style.background = perfil ? perfil.cor : "#5b6371";
  selo.title = perfil
    ? "tudo nesta barra e da conta desta aba"
    : "conta que ja estava logada antes do app, usada pelas abas sem conta escolhida";

  // sem login nao existe limite pra mostrar, e emprestar o de outra conta so confunde
  const semLogin = !!perfil && !perfil.logado;
  const faixas = semLogin ? {} : (limitesPorPerfil[chave] || {}).faixas || {};
  pintarMedidor("medidor-5h", faixas.five_hour, semLogin);
  pintarMedidor("medidor-7d", faixas.seven_day, semLogin);

  pintarMedidor("medidor-ctx", contextoDaAba());
  detalharContexto();

  pintarModelo();
  atualizarTituloDaJanela();
}

function atualizarLimites() {
  if (!bridge) return;
  bridge.getLimits((json) => {
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
  });
}

function atualizarUsoDaBarra() {
  if (!bridge || !cfg.mostrarUsoNaBarra) return;
  bridge.getUsage((json) => {
    try {
      usoPorPerfil = JSON.parse(json);
    } catch (e) {
      usoPorPerfil = {};
    }
    pintarUsoDaBarra();
  });
}

setInterval(atualizarUsoDaBarra, 30000);
setInterval(atualizarLimites, 1200);

/* ------------------------------------------------------------- atalhos */

window.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && !elBusca.hidden) {
    fecharBusca();
    return;
  }
  if (ev.key === "Escape") {
    const aberto = veuAberto();
    if (aberto) return fecharVeu(aberto);
    if (!elMenu.hidden) return esconderMenu();
  }
  if (veuAberto()) return;

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
  } else if (ev.ctrlKey && ev.code === "Tab") {
    ev.preventDefault();
    if (ordem.length > 1) {
      const i = ordem.indexOf(abaAtiva);
      ativar(ordem[(i + 1) % ordem.length]);
    }
  }
});

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
    if (aba) aba.escrever(data);
    else for (const a of ordem) if (!a.ehPainel && !a.sessionId) a.pendentes.push(data);
  });

  bridge.mexeram.connect(() => {
    atualizarSessoes();
    atualizarLimites();
  });

  bridge.exited.connect((id, code) => {
    const aba = porSessao.get(id);
    if (aba) {
      aba.encerrada(code);
      montarSessoes();
    }
  });

  bridge.getHome((casa) => {
    pastaPessoal = (casa || "").replace(/[\\/]+$/, "");
  });

  bridge.getConfig((jsonCfg) => {
    cfg = JSON.parse(jsonCfg);
    bridge.listShells((jsonShells) => {
      shells = JSON.parse(jsonShells);
      carregarFerramentas(() => {
        bridge.listProfiles((jsonPerfis) => {
          perfis = JSON.parse(jsonPerfis);
          const raiz = el("sobre-raiz");
          if (raiz) {
            raiz.textContent = perfis.length
              ? perfis[0].configDir.replace(/[\\/]perfis[\\/].*$/, "")
              : "~/.dbx-term";
          }
          montarSelecaoShells();
          montarSelecaoPerfis();
          montarSelecaoFerramentas();
          preencherCampos();
          ligarCampos();
          aplicarAlturaExternas();
          aplicarLarguraLateral();
          restaurarAbas((tinha) => {
            if (!tinha) abaPadrao();
          });
          atualizarSessoes();
          atualizarUsoDaBarra();
          atualizarLimites();

          const parametros = new URLSearchParams(location.search);
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

window.addEventListener("beforeunload", () => {
  for (const a of ordem) if (bridge && a.sessionId) bridge.closeSession(a.sessionId);
});
