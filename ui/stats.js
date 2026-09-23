"use strict";

/* Uso, dentro das Configuracoes.

   Uma conta por vez. Voce escolhe de quem quer ver e a tela inteira responde: os numeros,
   as reguas do plano e o grafico. Tabela larga com tudo junto nao cabia e nao se lia. */

const CAMPOS_SOMA = ["entrada", "saida", "raciocinio", "cacheEscrito", "cacheLido", "mensagens"];

let usoBruto = {};
let usoPerfis = [];
let usoLimites = {};
let usoParcial = false;
let contaEscolhida = "todas";
let faixaGrafico = "dia";

const DIAS_DO_GRAFICO = 30;

function curto(n) {
  if (!n) return "0";
  if (n >= 1e9) return (n / 1e9).toFixed(2) + "B";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1000) return (n / 1000).toFixed(1) + "k";
  return String(n);
}

function escapar(texto) {
  /* O apelido da conta e digitado por voce e vai pro innerHTML: com < ou & ele quebrava o
     botao, e com uma tag inteira virava HTML dentro de uma pagina que fala com os shells. */
  return String(texto).replace(/[&<>"']/g, (c) => "&#" + c.charCodeAt(0) + ";");
}

function nomeDeModelo(bruto) {
  /* O id do transcript vira nome de gente, o mesmo na barra e na tela de Uso: la saia
     sonnet-4-5, e na barra Sonnet 4 5, com espaco no lugar do ponto. O que ja vem bonito da
     status line passa direto. */
  if (!bruto) return "";
  const texto = String(bruto);
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

function nomeModelo(id) {
  return nomeDeModelo(id).replace(/[<>]/g, "");
}

function escolherConta(conta) {
  // a conta que a tela abre mostrando; se ela nao tiver consumo, a pintura volta pra Todas
  contaEscolhida = conta || "todas";
}

function diaCurto(iso) {
  const [, m, d] = iso.split("-");
  return d + "/" + m;
}

function somar(alvo, parcela) {
  for (const k of CAMPOS_SOMA) alvo[k] = (alvo[k] || 0) + ((parcela || {})[k] || 0);
  return alvo;
}

function juntarModelos(alvo, ponto) {
  alvo.porModelo = alvo.porModelo || {};
  for (const [m, v] of Object.entries(ponto.porModelo || {})) {
    alvo.porModelo[m] = (alvo.porModelo[m] || 0) + v;
  }
  return alvo;
}

function porSemana(serie) {
  const baldes = new Map();
  for (const ponto of serie) {
    const d = new Date(ponto.dia + "T00:00:00");
    const seg = new Date(d);
    seg.setDate(d.getDate() - ((d.getDay() + 6) % 7));
    // data local: o toISOString converte pra UTC e, a leste de Greenwich, a meia-noite da
    // segunda vira domingo, e a semana aparecia rotulada com o dia anterior
    const doisDigitos = (n) => String(n).padStart(2, "0");
    const chave = seg.getFullYear() + "-" + doisDigitos(seg.getMonth() + 1) + "-" + doisDigitos(seg.getDate());
    baldes.set(chave, juntarModelos(somar(baldes.get(chave) || { dia: chave }, ponto), ponto));
  }
  return [...baldes.values()].sort((a, b) => a.dia.localeCompare(b.dia));
}

function diaLocal(d) {
  const doisDigitos = (n) => String(n).padStart(2, "0");
  return d.getFullYear() + "-" + doisDigitos(d.getMonth() + 1) + "-" + doisDigitos(d.getDate());
}

function comDiasVazios(serie, hoje) {
  /* A serie so traz os dias com gasto e o grafico dividia a largura entre eles: dez dias
     espalhados no mes viravam dez barras coladas e pareciam dez dias seguidos. Aqui cada um
     dos 30 dias ganha a sua barra, zerada quando nao houve uso. */
  const porDia = new Map(serie.map((p) => [p.dia, p]));
  const fim = hoje ? new Date(hoje.getFullYear(), hoje.getMonth(), hoje.getDate()) : new Date();
  const cheia = [];
  for (let i = DIAS_DO_GRAFICO - 1; i >= 0; i -= 1) {
    const d = new Date(fim.getFullYear(), fim.getMonth(), fim.getDate() - i);
    const chave = diaLocal(d);
    cheia.push(porDia.get(chave) || { dia: chave, saida: 0, porModelo: {} });
  }
  return cheia;
}

function familiaDoModelo(id) {
  const nome = String(id).toLowerCase();
  const familia = Object.keys(CORES_MODELO).find((f) => nome.includes(f));
  return CORES_MODELO[familia] || "#5b6371";
}

/* ---------------------------------------------------------------- contas */

function contasDoUso() {
  const lista = [];
  let removidas = null;

  // Se a pasta de sempre virou conta cadastrada, o gasto antigo dela e da mesma conta.
  // Sem juntar, a tela mostra Principal e a conta lado a lado somando duas vezes.
  const dona = usoPerfis.find((p) => p.padrao);

  for (const chave of Object.keys(usoBruto)) {
    if (dona && chave === "__padrao__") continue;
    const perfil = usoPerfis.find((p) => p.id === chave);
    if (!perfil && chave !== "__padrao__") {
      // conta apagada do cadastro: o gasto continua no historico, mas um id solto
      // na tela nao diz nada a ninguem
      removidas = removidas || { id: "__removidas__", nome: "Removidas", cor: "#2f3540", email: "", chaves: [] };
      removidas.chaves.push(chave);
      continue;
    }
    const chaves = [chave];
    if (dona && perfil && perfil.id === dona.id && usoBruto["__padrao__"]) chaves.push("__padrao__");
    lista.push({
      id: chave,
      // o mesmo nome que o cartao e a barra dao pra conta de sempre
      nome: perfil ? perfil.nome : "Principal",
      cor: perfil ? perfil.cor : "#5b6371",
      email: perfil ? perfil.email || "" : "Principal, o login do ~/.claude",
      chaves,
    });
  }

  // Conta sem transcript ainda, como uma recem logada, ja pode ter a leitura do plano. Sem
  // entrar aqui, o limite dela nao aparecia em lugar nenhum da tela de Uso.
  for (const [chave, lido] of Object.entries(usoLimites || {})) {
    if (!lido || !lido.faixas || !Object.keys(lido.faixas).length) continue;
    const id = dona && chave === "__padrao__" ? dona.id : chave;
    if (lista.some((c) => c.id === id)) continue;
    const perfil = usoPerfis.find((p) => p.id === id);
    if (!perfil && id !== "__padrao__") continue;
    lista.push({
      id,
      nome: perfil ? perfil.nome : "Principal",
      cor: perfil ? perfil.cor : "#5b6371",
      email: perfil ? perfil.email || "" : "Principal, o login do ~/.claude",
      chaves: id === chave ? [id] : [id, chave],
    });
  }

  lista.sort((a, b) => (totalDe(b.chaves).semana.saida || 0) - (totalDe(a.chaves).semana.saida || 0));
  if (removidas) lista.push(removidas);
  return lista;
}

function limitesDeTodas(contas) {
  /* Em Todas, uma linha por conta com o 5h e o 7d. O bloco so existia com uma conta escolhida,
     e o botao Uso abre em Todas: quem queria saber quanto faltava de cada conta via so tokens. */
  const linhas = contas
    .filter((c) => c.id !== "__removidas__")
    .map((c) => [c, faixasDaConta(c)])
    .filter(([, f]) => f.five_hour || f.seven_day)
    .map(
      ([c, f]) =>
        '<div class="limite-conta"><div class="limite-conta-nome">' +
        '<span class="bolinha" style="background:' + escapar(c.cor) + '"></span>' + escapar(c.nome) + "</div>" +
        regua("5h", f.five_hour) + regua("7d", f.seven_day) + "</div>"
    );
  return linhas.length ? '<div class="bloco-limite"><h3>Limite do plano</h3>' + linhas.join("") + "</div>" : "";
}

function totalDe(chaves) {
  const soma = { hoje: {}, semana: {}, mes: {}, total: {} };
  const serie = new Map();
  const modelos = new Map();

  for (const chave of chaves) {
    const d = usoBruto[chave];
    if (!d) continue;
    somar(soma.hoje, d.hoje);
    somar(soma.semana, d.semana);
    somar(soma.mes, d.mes);
    somar(soma.total, d.total);
    for (const ponto of d.serie || []) {
      serie.set(ponto.dia, juntarModelos(somar(serie.get(ponto.dia) || { dia: ponto.dia }, ponto), ponto));
    }
    for (const m of d.modelos || []) modelos.set(m.modelo, (modelos.get(m.modelo) || 0) + m.tokens);
  }

  soma.serie = [...serie.values()].sort((a, b) => a.dia.localeCompare(b.dia));
  soma.modelos = [...modelos.entries()].filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
  return soma;
}

function faixasDaConta(conta) {
  /* A leitura do plano pelo id da conta. A conta de sempre que virou cadastrada junta o
     proprio id com o gasto antigo de __padrao__, e com duas chaves o bloco sumia pra ela;
     sem leitura no id, vale a mais recente entre as que ela junta. */
  const limites = usoLimites || {};
  const propria = (limites[conta.id] || {}).faixas;
  if (propria && Object.keys(propria).length) return propria;
  let melhor = null;
  for (const chave of conta.chaves) {
    const lido = limites[chave];
    if (!lido || !lido.faixas || !Object.keys(lido.faixas).length) continue;
    if (!melhor || (lido.lido || 0) > (melhor.lido || 0)) melhor = lido;
  }
  return melhor ? melhor.faixas : {};
}

function selecionadas() {
  const contas = contasDoUso();
  if (contaEscolhida === "todas") return contas.flatMap((c) => c.chaves);
  const alvo = contas.find((c) => c.id === contaEscolhida);
  return alvo ? alvo.chaves : [];
}

/* --------------------------------------------------------------- pedacos */

function numero(rotulo, valor, apoio) {
  return (
    '<div class="numero"><span class="numero-rotulo">' + rotulo + "</span>" +
    '<span class="numero-valor">' + curto(valor) + "</span>" +
    '<span class="numero-apoio">' + apoio + "</span></div>"
  );
}

function apoioDoNumero(periodo) {
  // o numero grande sozinho nao dizia se era token, caractere ou dinheiro
  const respostas = periodo.mensagens || 0;
  return "tokens gerados em " + respostas + (respostas === 1 ? " resposta" : " respostas");
}

function regua(rotulo, faixa) {
  if (!faixa || typeof faixa.usado !== "number") {
    return '<div class="regua-linha"><span class="regua-rotulo">' + rotulo + "</span>" +
      '<span class="regua-vazia">Sem leitura ainda</span></div>';
  }
  const pct = Math.round(faixa.usado);
  const classe = pct >= 85 ? "critico" : pct >= 60 ? "atencao" : "";
  // quando zera e o que a barra de baixo mostra, e quem abria o detalhe achava menos que la
  const falta = typeof faltaPara === "function" ? faltaPara(faixa.zera) : "";
  return (
    '<div class="regua-linha"><span class="regua-rotulo">' + rotulo + "</span>" +
    '<span class="regua ' + classe + '"><span class="trilho"><i style="width:' + Math.min(pct, 100) + '%"></i></span>' +
    "<b>" + pct + "%</b>" +
    (falta ? '<span class="regua-zera">zera em ' + escapar(falta) + "</span>" : "") +
    "</span></div>"
  );
}

function grafico(serie) {
  if (!serie.some((d) => d.saida)) return '<div class="vazio">Sem consumo no período.</div>';

  const larg = 620;
  const alt = 110;
  const baixo = 16;
  const teto = Math.max(...serie.map((d) => d.saida), 1);
  const passo = larg / serie.length;
  const grossura = Math.min(passo - 3, 16);
  const util = alt - baixo;

  const barras = serie
    .map((d, i) => {
      const h = Math.max((d.saida / teto) * util, d.saida ? 2 : 0);
      const x = i * passo + (passo - grossura) / 2;

      // empilhada por modelo, do que mais gastou pro que menos, como o PLANO pede
      const fatias = Object.entries(d.porModelo || {})
        .filter(([, v]) => v > 0)
        .sort((a, b) => b[1] - a[1]);
      const soma = fatias.reduce((t, [, v]) => t + v, 0);

      let pedacos;
      let dica = diaCurto(d.dia) + " · " + curto(d.saida) + " gerados";
      if (soma > 0) {
        let base = util;
        pedacos = fatias
          .map(([modelo, valor]) => {
            const alto = Math.max((valor / soma) * h, 1);
            base -= alto;
            return (
              // no estilo, e nao no atributo: a regra .fito do CSS venceria o atributo
              '<rect class="fito" x="' + x + '" y="' + base + '" width="' + grossura +
              '" height="' + alto + '" style="fill:' + familiaDoModelo(modelo) + '"></rect>'
            );
          })
          .join("");
        dica += fatias.map(([m, v]) => "\n" + nomeModelo(m) + " " + curto(v)).join("");
      } else {
        pedacos =
          '<rect class="fito" x="' + x + '" y="' + (util - h) + '" width="' + grossura +
          '" height="' + h + '" rx="2"></rect>';
      }

      return (
        '<g class="barra">' +
        '<rect class="alvo" x="' + i * passo + '" y="0" width="' + passo + '" height="' + util + '"></rect>' +
        pedacos +
        "<title>" + dica + "</title></g>"
      );
    })
    .join("");

  const pontas = [0, serie.length - 1]
    .map((i) => {
      const x = i === 0 ? 0 : larg;
      const ancora = i === 0 ? "start" : "end";
      return '<text class="rotulo-dia" x="' + x + '" y="' + (alt - 3) + '" text-anchor="' + ancora + '">' +
        diaCurto(serie[i].dia) + "</text>";
    })
    .join("");

  return (
    '<svg viewBox="0 0 ' + larg + " " + alt + '" preserveAspectRatio="none">' + barras + pontas + "</svg>" +
    '<div class="grafico-teto">pico de ' + curto(teto) + (faixaGrafico === "semana" ? " numa semana" : " num dia") + "</div>"
  );
}

/* ----------------------------------------------------------------- tela */

function pintarUsoCorpo() {
  const alvo = document.getElementById("corpo-uso");
  if (!alvo) return;

  const contas = contasDoUso();
  if (!contas.length) {
    alvo.innerHTML = usoParcial
      ? '<div class="vazio">Lendo o histórico das conversas. Os números aparecem em instantes.</div>'
      : '<div class="vazio">Nenhum consumo encontrado ainda. O medidor lê os transcripts que o Claude ' +
        "grava em disco, então o número aparece depois da primeira conversa.</div>";
    return;
  }

  if (contaEscolhida !== "todas" && !contas.some((c) => c.id === contaEscolhida)) contaEscolhida = "todas";

  const filtro =
    '<div class="filtro-contas">' +
    '<button class="chip' + (contaEscolhida === "todas" ? " ativo" : "") + '" data-conta="todas">Todas</button>' +
    contas
      .map(
        (c) =>
          '<button class="chip' + (contaEscolhida === c.id ? " ativo" : "") + '" data-conta="' + escapar(c.id) + '" ' +
          (c.email ? 'title="' + escapar(c.email) + '"' : "") + ">" +
          '<span class="bolinha" style="background:' + escapar(c.cor) + '"></span>' + escapar(c.nome) + "</button>"
      )
      .join("") +
    "</div>";

  const dados = totalDe(selecionadas());
  const uma = contaEscolhida !== "todas" ? contas.find((c) => c.id === contaEscolhida) : null;
  const faixas = uma && uma.id !== "__removidas__" ? faixasDaConta(uma) : null;

  const numeros =
    '<div class="numeros">' +
    numero("Hoje", dados.hoje.saida || 0, apoioDoNumero(dados.hoje)) +
    numero("Esta semana", dados.semana.saida || 0, apoioDoNumero(dados.semana)) +
    numero("Este mês", dados.mes.saida || 0, apoioDoNumero(dados.mes)) +
    "</div>";

  const limite = faixas
    ? '<div class="bloco-limite"><h3>Limite do plano</h3>' + regua("5h", faixas.five_hour) + regua("7d", faixas.seven_day) + "</div>"
    : contaEscolhida === "todas"
    ? limitesDeTodas(contas)
    : "";

  const modelos = dados.modelos.length
    ? '<div class="modelos-uso">' +
      dados.modelos.map(([m, v]) => '<span class="pilula-modelo"><span class="bolinha" style="background:' + familiaDoModelo(m) + '"></span>' + nomeModelo(m) + " <b>" + curto(v) + "</b></span>").join("") +
      "</div>"
    : "";

  const serie = comDiasVazios(dados.serie);
  // a varredura le um pedaco por vez de historico grande; ate terminar, o numero e menor que
  // o real, e a tela dizia isso com a mesma cara de numero medido
  const ajuda = usoParcial
    ? '<p class="ajuda ajuda-lendo">Ainda lendo o histórico, os números vão subir até terminar.</p>'
    : '<p class="ajuda">Tokens gerados, lidos dos transcripts em disco. Escolha a conta pra ver só o dela.</p>';

  alvo.innerHTML =
    ajuda +
    filtro +
    numeros +
    limite +
    '<div class="bloco-topo"><h3>Últimos 30 dias</h3>' +
    '<div class="filtro" id="filtro-grafico">' +
    '<button class="aba-filtro' + (faixaGrafico === "dia" ? " ativa" : "") + '" data-faixa="dia">Dia</button>' +
    '<button class="aba-filtro' + (faixaGrafico === "semana" ? " ativa" : "") + '" data-faixa="semana">Semana</button>' +
    "</div></div>" +
    '<div id="area-grafico">' + grafico(faixaGrafico === "semana" ? porSemana(serie) : serie) + "</div>" +
    modelos;

  alvo.querySelectorAll("[data-conta]").forEach((b) => {
    b.addEventListener("click", () => {
      contaEscolhida = b.dataset.conta;
      pintarUsoCorpo();
    });
  });
  alvo.querySelectorAll("#filtro-grafico .aba-filtro").forEach((b) => {
    b.addEventListener("click", () => {
      faixaGrafico = b.dataset.faixa;
      pintarUsoCorpo();
    });
  });
}

function renderStats(dados, perfis, limites, parcial) {
  usoBruto = dados || {};
  usoPerfis = perfis || [];
  usoLimites = limites || {};
  usoParcial = !!parcial;
  pintarUsoCorpo();
}
