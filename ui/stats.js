"use strict";

/* Painel de estatisticas de uso.
   Serie unica de proposito: o que importa por dia e o total gerado, e uma barra
   por dia le melhor do que quatro series empilhadas disputando a mesma altura.
   A quebra por modelo e por conta vive em lista, com barra proporcional. */

const MIL = 1000;

function curto(n) {
  if (!n) return "0";
  if (n >= 1e9) return (n / 1e9).toFixed(2) + " B";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + " M";
  if (n >= MIL) return (n / MIL).toFixed(1) + " k";
  return String(n);
}

function nomeModelo(id) {
  return id
    .replace(/^claude-/, "")
    .replace(/-\d{8}$/, "")
    .replace(/^<|>$/g, "");
}

function diaCurto(iso) {
  const [, m, d] = iso.split("-");
  return d + "/" + m;
}

function cartao(titulo, dados) {
  const d = dados || {};
  return (
    '<div class="cartao">' +
    '<div class="cartao-titulo">' + titulo + "</div>" +
    '<div class="cartao-valor">' + curto(d.saida || 0) + "</div>" +
    '<div class="cartao-rodape">gerados · ' + (d.mensagens || 0) + " respostas</div>" +
    '<div class="cartao-detalhe">' +
    "<span>raciocinio " + curto(d.raciocinio || 0) + "</span>" +
    "<span>cache lido " + curto(d.cacheLido || 0) + "</span>" +
    "</div></div>"
  );
}

function grafico(serie) {
  if (!serie.length) return '<div class="vazio">Sem consumo nos ultimos 30 dias.</div>';

  const larg = 760;
  const alt = 190;
  const baixo = 26;
  const topo = 14;
  const teto = Math.max(...serie.map((d) => d.saida), 1);
  const passo = larg / serie.length;
  const grossura = Math.min(passo - 2, 22);
  const util = alt - baixo - topo;

  const grades = [0.25, 0.5, 0.75, 1]
    .map((f) => {
      const y = topo + util * (1 - f);
      return (
        '<line class="grade" x1="0" x2="' + larg + '" y1="' + y + '" y2="' + y + '"></line>' +
        '<text class="marca-eixo" x="0" y="' + (y - 5) + '">' + curto(Math.round(teto * f)) + "</text>"
      );
    })
    .join("");

  const pico = serie.reduce((a, b) => (b.saida > a.saida ? b : a), serie[0]);

  const barras = serie
    .map((d, i) => {
      const h = Math.max((d.saida / teto) * util, d.saida ? 2 : 0);
      const x = i * passo + (passo - grossura) / 2;
      const y = topo + util - h;
      const titulo =
        diaCurto(d.dia) + " · " + curto(d.saida) + " gerados · " + (d.mensagens || 0) + " respostas";
      return (
        '<g class="barra">' +
        '<rect class="alvo" x="' + i * passo + '" y="' + topo + '" width="' + passo + '" height="' + util + '"></rect>' +
        '<rect class="fito" x="' + x + '" y="' + y + '" width="' + grossura + '" height="' + h + '" rx="3"></rect>' +
        "<title>" + titulo + "</title>" +
        "</g>"
      );
    })
    .join("");

  const rotulos = serie
    .map((d, i) => {
      const mostrar = i === 0 || i === serie.length - 1 || d.dia === pico.dia;
      if (!mostrar) return "";
      const x = i * passo + passo / 2;
      const ancora = i === 0 ? "start" : i === serie.length - 1 ? "end" : "middle";
      return '<text class="rotulo-dia" x="' + x + '" y="' + (alt - 8) + '" text-anchor="' + ancora + '">' + diaCurto(d.dia) + "</text>";
    })
    .join("");

  return (
    '<svg class="gráfico" viewBox="0 0 ' + larg + " " + alt + '" preserveAspectRatio="none">' +
    grades + barras + rotulos +
    "</svg>"
  );
}

function listaModelos(modelos) {
  if (!modelos || !modelos.length) return "";
  const teto = Math.max(...modelos.map((m) => m.tokens), 1);
  return (
    '<div class="bloco"><h3>Por modelo, ultimos 30 dias</h3>' +
    modelos
      .filter((m) => m.tokens > 0)
      .map(
        (m) =>
          '<div class="faixa">' +
          '<span class="faixa-nome">' + nomeModelo(m.modelo) + "</span>" +
          '<span class="faixa-trilho"><i style="width:' + Math.max((m.tokens / teto) * 100, 1) + '%"></i></span>' +
          '<span class="faixa-valor">' + curto(m.tokens) + "</span>" +
          "</div>"
      )
      .join("") +
    "</div>"
  );
}

function listaContas(entradas) {
  return (
    '<div class="bloco"><h3>Por conta</h3>' +
    '<table class="tabela"><thead><tr>' +
    "<th>conta</th><th>hoje</th><th>semana</th><th>mes</th><th>respostas na semana</th><th>ultimo uso</th>" +
    "</tr></thead><tbody>" +
    entradas
      .map(
        (e) =>
          "<tr>" +
          '<td><span class="bolinha" style="background:' + e.cor + '"></span>' + e.nome + "</td>" +
          "<td>" + curto(e.dados.hoje.saida || 0) + "</td>" +
          "<td>" + curto(e.dados.semana.saida || 0) + "</td>" +
          "<td>" + curto(e.dados.mes.saida || 0) + "</td>" +
          "<td>" + (e.dados.semana.mensagens || 0) + "</td>" +
          "<td>" + (e.dados.ultimoDia ? diaCurto(e.dados.ultimoDia) : "nunca") + "</td>" +
          "</tr>"
      )
      .join("") +
    "</tbody></table></div>"
  );
}

function somar(alvo, parcela) {
  for (const k of ["entrada", "saida", "raciocinio", "cacheEscrito", "cacheLido", "mensagens"]) {
    alvo[k] = (alvo[k] || 0) + (parcela[k] || 0);
  }
  return alvo;
}

function porSemana(serie) {
  const baldes = new Map();
  for (const ponto of serie) {
    const d = new Date(ponto.dia + "T00:00:00");
    const seg = new Date(d);
    seg.setDate(d.getDate() - ((d.getDay() + 6) % 7));
    const chave = seg.toISOString().slice(0, 10);
    const alvo = baldes.get(chave) || { dia: chave };
    baldes.set(chave, somar(alvo, ponto));
  }
  return [...baldes.values()].sort((a, b) => a.dia.localeCompare(b.dia));
}

let FAIXA_GRAFICO = "dia";
let SERIE_GUARDADA = [];

function pintarGrafico() {
  const alvo = document.getElementById("area-grafico");
  if (!alvo) return;
  const serie = FAIXA_GRAFICO === "semana" ? porSemana(SERIE_GUARDADA) : SERIE_GUARDADA;
  alvo.innerHTML = grafico(serie);
  document.querySelectorAll("#filtro-grafico .aba-filtro").forEach((b) => {
    b.classList.toggle("ativa", b.dataset.faixa === FAIXA_GRAFICO);
  });
}

function ligarFiltroGrafico() {
  document.querySelectorAll("#filtro-grafico .aba-filtro").forEach((b) => {
    b.addEventListener("click", () => {
      FAIXA_GRAFICO = b.dataset.faixa;
      pintarGrafico();
    });
  });
}

function renderStats(dados, perfis) {
  const chaves = Object.keys(dados);
  if (!chaves.length) {
    return (
      '<div class="stats"><div class="vazio">' +
      "Nenhum consumo encontrado ainda. O medidor le os transcript que o Claude Code grava em disco, " +
      "entao o numero aparece depois da primeira conversa dentro de uma aba do DBX Term." +
      "</div></div>"
    );
  }

  const geral = { hoje: {}, semana: {}, mes: {} };
  const entradas = [];
  const serieGeral = new Map();
  const modelosGeral = new Map();

  for (const chave of chaves) {
    const d = dados[chave];
    const perfil = perfis.find((p) => p.id === chave);
    entradas.push({
      nome: perfil ? perfil.nome : "conta de antes do app",
      cor: perfil ? perfil.cor : "#5b6371",
      dados: d,
    });
    somar(geral.hoje, d.hoje);
    somar(geral.semana, d.semana);
    somar(geral.mes, d.mes);
    for (const ponto of d.serie) {
      serieGeral.set(ponto.dia, somar(serieGeral.get(ponto.dia) || { dia: ponto.dia }, ponto));
    }
    for (const m of d.modelos) {
      modelosGeral.set(m.modelo, (modelosGeral.get(m.modelo) || 0) + m.tokens);
    }
  }

  const serie = [...serieGeral.values()].sort((a, b) => a.dia.localeCompare(b.dia));
  const modelos = [...modelosGeral.entries()]
    .map(([modelo, tokens]) => ({ modelo, tokens }))
    .sort((a, b) => b.tokens - a.tokens);

  SERIE_GUARDADA = serie;

  return (
    '<div class="stats">' +
    '<div class="stats-topo"><h1>Uso</h1>' +
    '<span class="stats-nota">medido nos transcript em disco, nao e o limite do plano</span></div>' +
    '<div class="cartoes">' +
    cartao("Hoje", geral.hoje) +
    cartao("Esta semana", geral.semana) +
    cartao("Este mes", geral.mes) +
    "</div>" +
    '<div class="bloco">' +
    '<div class="bloco-topo"><h3>Tokens gerados</h3>' +
    '<div class="filtro" id="filtro-grafico">' +
    '<button class="aba-filtro ativa" data-faixa="dia">por dia</button>' +
    '<button class="aba-filtro" data-faixa="semana">por semana</button>' +
    "</div></div>" +
    '<div id="area-grafico"></div></div>' +
    listaContas(entradas) +
    listaModelos(modelos) +
    "</div>"
  );
}
