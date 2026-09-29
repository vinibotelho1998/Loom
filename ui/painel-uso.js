"use strict";

/* Painel de uso a direita dos terminais, no desenho do CodexBar, de Peter Steinberger, sob a
   licenca MIT: abas de provedor, contas, cartao com as janelas de 5h e da semana, o custo e o
   grafico dos ultimos 30 dias.

   O relatorio chega pronto do app, pelo getPainelUso e pelo sinal painelUsoPronto. Aqui so se
   calcula o que depende do relogio: ritmo, projecao, zera em e atualizado ha. Todo texto e
   todo HTML saem de funcoes puras, que o teste roda sem navegador; o resto so liga eventos. */

const PainelUso = (function () {
  const LARGURA_PADRAO = 310;
  const LARGURA_MINIMA = 280;
  const LARGURA_MAXIMA = 480;
  // o relatorio custa ao app uma varredura dos logs numa thread; o texto que envelhece e barato
  const PEDIR_A_CADA_MS = 60000;
  const REDESENHAR_A_CADA_MS = 30000;
  // arrastando a alca, cada pixel remedia o terminal; ele espera a mao parar, como na lateral
  const ESPERA_DO_ENCAIXE_MS = 300;
  const TRACO = "--";
  const MESES = ["jan.", "fev.", "mar.", "abr.", "mai.", "jun.", "jul.", "ago.", "set.", "out.", "nov.", "dez."];
  const AVISO_ESTIMATIVA = "Estimado pelos logs locais · pode diferir da fatura";
  const AVISO_SEM_PRECO = "Há modelo sem preço na tabela; o custo soma só os que têm preço.";
  const AVISO_APROXIMADO =
    "Parte dos dias veio do cache antigo, sem separar o cache de 1h e o de 5m; o custo é aproximado.";
  const DICA_ESTIMADA = "Estimado pela regra do bloco de 5h, sem o limite lido da própria ferramenta";
  // o CodexBar marca 50% e 20% restantes na sessao e na semana
  const LIMIARES = [50, 20];

  const ICONE_CLAUDE = '<path d="M25.7146 63.2153L41.4393 54.3917L41.7025 53.6226L41.4393 53.1976H40.6705L38.0394 53.0359L29.054 52.7929L21.2624 52.4691L13.7134 52.0644L11.8111 51.6594L10.0303 49.3118L10.2123 48.138L11.8111 47.0657L14.0981 47.2681L19.1574 47.6119L26.7467 48.138L32.2516 48.4618L40.4073 49.3118H41.7025L41.8846 48.7857L41.4393 48.4618L41.0955 48.138L33.243 42.8155L24.7432 37.1894L20.2909 33.9513L17.8824 32.3119L16.6684 30.774L16.1422 27.4147L18.328 25.0062L21.2624 25.2088L22.0112 25.4112L24.9861 27.6979L31.3407 32.616L39.6381 38.7273L40.8525 39.7391L41.3381 39.395L41.399 39.1523L40.8525 38.2415L36.3394 30.0858L31.5227 21.7883L29.3775 18.3478L28.811 16.2837C28.6087 15.4334 28.4669 14.7252 28.4669 13.8549L30.9563 10.4753L32.3321 10.0303L35.6515 10.4756L37.0479 11.6897L39.112 16.4052L42.4513 23.8327L47.6321 33.9313L49.15 36.9265L49.9594 39.6991L50.2632 40.5491H50.7894V40.0632L51.2141 34.3766L52.0035 27.3944L52.7726 18.4087L53.0358 15.8793L54.2905 12.8435L56.7795 11.2041L58.7224 12.135L60.3212 14.422L60.0986 15.899L59.1474 22.0718L57.2857 31.7458L56.0713 38.2218H56.7795L57.5892 37.4121L60.8677 33.061L66.3723 26.18L68.801 23.448L71.6342 20.4325L73.4556 18.9957H76.8962L79.4255 22.7601L78.2926 26.6456L74.7509 31.1384L71.8163 34.943L67.607 40.6097L64.9758 45.1431L65.2188 45.5072L65.8464 45.4466L75.358 43.4228L80.4984 42.4917L86.6304 41.4393L89.4033 42.7346L89.7065 44.0502L88.6135 46.7419L82.0566 48.3607L74.3662 49.8989L62.9118 52.6109L62.77 52.7121L62.9321 52.9144L68.0925 53.4L70.2987 53.5214H75.7021L85.7601 54.2702L88.3912 56.0108L89.9697 58.1358L89.7065 59.7545L85.6589 61.8189L80.1949 60.5236L67.4452 57.4881L63.0735 56.3952H62.4665V56.7596L66.1093 60.3213L72.7877 66.3523L81.1461 74.1236L81.5707 76.0462L80.4984 77.5638L79.3649 77.4021L72.0186 71.8772L69.1854 69.3879L62.77 63.9844H62.3453V64.5509L63.8223 66.7164L71.6342 78.4544L72.0389 82.0567L71.4725 83.2308L69.4487 83.939L67.2222 83.534L62.6485 77.1189L57.9333 69.8937L54.1284 63.4177L53.6631 63.6809L51.4167 87.8651L50.3644 89.0995L47.9356 90.0303L45.9121 88.4924L44.8392 86.0031L45.9118 81.0852L47.2071 74.6701L48.2594 69.5699L49.2106 63.2356L49.7773 61.131L49.7367 60.9892L49.2715 61.0498L44.4954 67.607L37.23 77.4224L31.4825 83.5746L30.1063 84.1211L27.7181 82.8864L27.9408 80.6805L29.2763 78.7177L37.2297 68.5988L42.026 62.3248L45.1227 58.7025L45.1024 58.176H44.9204L23.7917 71.8975L20.0274 72.3831L18.4083 70.8655L18.6106 68.3761L19.3798 67.5664L25.7343 63.195L25.7146 63.2153Z"/>';
  const ICONE_CODEX = '<path d="M83.7733 42.8087C84.6678 40.1149 84.9771 37.2613 84.6807 34.4385C84.3843 31.6156 83.489 28.8885 82.0544 26.4394C77.6908 18.8436 68.9203 14.9365 60.3548 16.7725C57.9831 14.1344 54.9591 12.1668 51.5864 11.0673C48.2137 9.96772 44.611 9.77498 41.1402 10.5084C37.6694 11.2418 34.4527 12.8755 31.8132 15.2455C29.1736 17.6155 27.204 20.6383 26.1024 24.0103C23.3212 24.5806 20.6938 25.738 18.3958 27.405C16.0977 29.0721 14.1819 31.2104 12.7765 33.6772C8.36538 41.2609 9.3669 50.8267 15.2527 57.3327C14.3549 60.0251 14.0424 62.8782 14.3361 65.7012C14.6298 68.5241 15.523 71.2518 16.9558 73.7017C21.325 81.3002 30.1011 85.207 38.6712 83.3686C40.5554 85.4904 42.8707 87.1858 45.4623 88.3416C48.0539 89.4975 50.8622 90.0871 53.6999 90.0713C62.4793 90.079 70.2575 84.4114 72.9393 76.0515C75.7201 75.4802 78.347 74.3225 80.6449 72.6555C82.9427 70.9886 84.8587 68.8507 86.2649 66.3846C90.6227 58.8145 89.6172 49.3005 83.7733 42.8087ZM53.6999 84.8356C50.1955 84.8411 46.801 83.6129 44.1116 81.3661L44.5848 81.098L60.5123 71.9043C60.9087 71.6718 61.2379 71.3402 61.4674 70.942C61.6969 70.5439 61.8189 70.0929 61.8215 69.6333V47.1769L68.5553 51.072C68.6225 51.1063 68.6694 51.1707 68.6814 51.2456V69.854C68.6641 78.1208 61.9667 84.8183 53.6999 84.8356ZM21.4977 71.0843C19.7402 68.0497 19.1092 64.4925 19.7156 61.0386L20.1885 61.3225L36.1321 70.5165C36.5266 70.748 36.9757 70.87 37.4331 70.87C37.8905 70.87 38.3396 70.748 38.7341 70.5165L58.21 59.2883V67.0628C58.2081 67.1031 58.1973 67.1424 58.1782 67.1779C58.1591 67.2134 58.1322 67.2441 58.0996 67.2678L41.9671 76.5722C34.798 80.7022 25.6388 78.2463 21.4977 71.0843ZM17.3026 36.3898C19.0723 33.3357 21.8655 31.0062 25.1878 29.8138V48.7376C25.1818 49.1949 25.2986 49.6453 25.5261 50.042C25.7535 50.4387 26.0833 50.7671 26.4809 50.9928L45.8622 62.1739L39.1283 66.069C39.0919 66.0883 39.0513 66.0984 39.0101 66.0984C38.9689 66.0984 38.9283 66.0883 38.8919 66.069L22.7908 56.7809C15.6359 52.6337 13.1822 43.4816 17.3026 36.3112V36.3898ZM72.624 49.2426L53.1792 37.9512L59.8976 34.0718C59.9341 34.0524 59.9747 34.0423 60.016 34.0423C60.0573 34.0423 60.0979 34.0524 60.1344 34.0718L76.2355 43.3761C78.6973 44.7966 80.7043 46.8882 82.0221 49.4065C83.3398 51.9249 83.914 54.7661 83.6775 57.5985C83.4411 60.431 82.4038 63.1377 80.6867 65.4027C78.9696 67.6677 76.6436 69.3975 73.9803 70.3901V51.466C73.9663 51.0096 73.834 50.5647 73.5962 50.1749C73.3584 49.7851 73.0234 49.4638 72.624 49.2426ZM79.3261 39.1657L78.8529 38.8815L62.9411 29.6089C62.5442 29.376 62.0924 29.2532 61.6322 29.2532C61.172 29.2532 60.7202 29.376 60.3233 29.6089L40.8629 40.8374V33.0628C40.8587 33.0233 40.8654 32.9834 40.882 32.9473C40.8987 32.9113 40.9248 32.8803 40.9575 32.8579L57.0586 23.5692C59.5263 22.1476 62.3478 21.458 65.193 21.5811C68.0382 21.7042 70.7896 22.6348 73.1253 24.2642C75.461 25.8936 77.2845 28.1543 78.3825 30.782C79.4806 33.4097 79.8077 36.2957 79.3257 39.1025V39.1657H79.3261ZM37.1888 52.9484L30.455 49.069C30.4213 49.0487 30.3925 49.0212 30.3707 48.9884C30.3488 48.9557 30.3345 48.9186 30.3286 48.8797V30.3188C30.3323 27.4714 31.1466 24.6839 32.6761 22.2822C34.2057 19.8805 36.3874 17.9639 38.9661 16.7564C41.5448 15.549 44.4139 15.1005 47.2381 15.4636C50.0622 15.8267 52.7247 16.9862 54.9141 18.8067L54.4409 19.0748L38.5134 28.2686C38.117 28.5011 37.7879 28.8327 37.5584 29.2308C37.329 29.629 37.207 30.0799 37.2045 30.5395L37.1888 52.9487V52.9484ZM40.8472 45.0632L49.5209 40.0643L58.21 45.0635V55.0615L49.5523 60.0608L40.8632 55.0615L40.8472 45.0632Z"/>';
  const ICONE_OUTRO = '<circle cx="50" cy="50" r="30"/>';
  const ICONE_COPIAR =
    '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round">' +
    '<rect x="5.5" y="5.5" width="8" height="8" rx="1.5"/><path d="M10.5 3.5v-.5A1.5 1.5 0 0 0 9 1.5H4A1.5 1.5 0 0 0 2.5 3v5A1.5 1.5 0 0 0 4 9.5h.5"/></svg>';
  const ICONE_COPIADO =
    '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M3.5 8.5 6.5 11.5 12.5 4.5"/></svg>';

  // cor de marca de cada provedor, a do CodexBar; o que ele nao conhece fica no acento do Loom
  const PROVEDORES = {
    claude: { nome: "Claude", marca: "#CC7C5E", icone: ICONE_CLAUDE },
    codex: { nome: "Codex", marca: "#49A3B0", icone: ICONE_CODEX },
    gemini: { nome: "Gemini", marca: "#AB87EA", icone: ICONE_OUTRO },
  };

  const JANELAS = {
    "5h": { titulo: "Sessão", contexto: "sessao", minutos: 300, ordem: 0 },
    "7d": { titulo: "Semanal", contexto: "semanal", minutos: 10080, ordem: 1 },
    // conta do Codex que so tem credito do mes, como o CodexBar chama
    "30d": { titulo: "Mensal", contexto: "semanal", minutos: 43200, ordem: 2 },
  };

  /* ------------------------------------------------------------ regras */

  function prender(v, menor, maior) {
    return Math.min(maior, Math.max(menor, v));
  }

  function meioPar(x) {
    // o %.0f do CodexBar arredonda o meio pro par: 42,5 vira 42, e o painel tem que dizer o mesmo
    const inteiro = Math.floor(x);
    const resto = x - inteiro;
    if (resto > 0.5) return inteiro + 1;
    if (resto < 0.5) return inteiro;
    return inteiro % 2 === 0 ? inteiro : inteiro + 1;
  }

  function numero(v) {
    return typeof v === "number" && Number.isFinite(v) ? v : null;
  }

  function positivo(v) {
    // dia com valor negativo e ignorado no grafico, como no CodexBar
    const n = numero(v);
    return n !== null && n > 0 ? n : 0;
  }

  function textoPercentual(p, sufixo) {
    const c = prender(p, 0, 100);
    if (c > 0 && c < 1) return "<1% " + sufixo;
    return meioPar(c) + "% " + sufixo;
  }

  function larguraPreenchimento(p) {
    // some abaixo de 0,5% e enche a partir de 99,5%, pra bater com o numero arredondado
    const c = prender(p, 0, 100);
    const r = Math.round(c);
    if (r <= 0) return 0;
    if (r >= 100) return 100;
    return c;
  }

  function contagem(alvoMs, agoraMs) {
    const s = (alvoMs - agoraMs) / 1000;
    if (!Number.isFinite(s)) return null;
    const total = s < 1 ? 0 : Math.max(1, Math.ceil(s / 60));
    if (total === 0) return "agora";
    const d = Math.floor(total / 1440);
    const h = Math.floor(total / 60) % 24;
    const m = total % 60;
    if (d > 0) return h > 0 ? d + "d " + h + "h" : m > 0 ? d + "d " + m + "m" : d + "d";
    if (h > 0) return m > 0 ? h + "h " + m + "m" : h + "h";
    return total + "m";
  }

  function textoZera(alvoMs, agoraMs) {
    const c = contagem(alvoMs, agoraMs);
    if (c === null) return "";
    return c === "agora" ? "Renova agora" : "Renova em " + c;
  }

  function doisDigitos(n) {
    return (n < 10 ? "0" : "") + n;
  }

  function horaCurta(ms) {
    const d = new Date(ms);
    return doisDigitos(d.getHours()) + ":" + doisDigitos(d.getMinutes());
  }

  function dataCurta(ms) {
    const d = new Date(ms);
    return d.getDate() + " de " + MESES[d.getMonth()];
  }

  function diaDaSerie(dia) {
    // o dia vem sem hora nem fuso; passar pelo Date o jogaria pro dia anterior a oeste de Greenwich
    const partes = String(dia).split("-");
    const mes = Number(partes[1]);
    const d = Number(partes[2]);
    return d > 0 && mes >= 1 && mes <= 12 ? d + " de " + MESES[mes - 1] : String(dia);
  }

  function textoAtualizado(ms, agoraMs) {
    const s = Math.trunc((agoraMs - ms) / 1000);
    // o app e a tela leem o mesmo relogio: data a frente e so o arredondamento, entao e agora
    if (s < 60) return "Atualizado agora mesmo";
    if (s < 3600) return "Atualizado há " + Math.floor(s / 60) + " min.";
    if (s < 24 * 3600) return "Atualizado há " + Math.floor(s / 3600) + " h";
    // o CodexBar mostra so a hora; num painel sempre aberto, dado de dois dias atras precisa do dia
    return "Atualizado " + dataCurta(ms) + " " + horaCurta(ms);
  }

  function estagio(delta) {
    const a = Math.abs(delta);
    if (a <= 2) return "no-ritmo";
    const lado = delta >= 0 ? "adiantado" : "atrasado";
    return a <= 6 ? lado + "-leve" : a <= 12 ? lado : lado + "-muito";
  }

  function ritmo(janela, agoraMs, padraoMin) {
    /* Onde o uso deveria estar agora, se fosse gasto por igual ate zerar, e quando acaba no
       passo de agora. O mesmo calculo do UsagePace do CodexBar, sem dias uteis. */
    const zeraEmMs = janela.zeraEmMs;
    if (zeraEmMs == null) return null;
    const minutos = janela.janelaMin > 0 ? janela.janelaMin : padraoMin;
    if (!(minutos > 0)) return null;
    const duracao = minutos * 60;
    const falta = (zeraEmMs - agoraMs) / 1000;
    if (!(falta > 0) || falta > duracao) return null;
    const decorrido = prender(duracao - falta, 0, duracao);
    const esperado = prender((decorrido / duracao) * 100, 0, 100);
    const real = prender(janela.usado, 0, 100);
    if (decorrido === 0 && real > 0) return null;
    const delta = real - esperado;
    let eta = null;
    let duraAteZerar = false;
    if (real >= 100) eta = 0;
    else if (decorrido > 0 && real > 0) {
      const candidato = (100 - real) / (real / decorrido);
      if (candidato >= falta) duraAteZerar = true;
      else eta = candidato;
    } else if (decorrido > 0 && real === 0) duraAteZerar = true;
    const projetado = decorrido > 0 ? (real * falta) / decorrido : 0;
    const folgaVel = 100 - real > 0 && projetado > 0 ? (100 - real) / projetado : null;
    return { delta, esperado, real, eta, duraAteZerar, folgaVel, estagio: estagio(delta) };
  }

  function ritmoVisivel(r, contexto) {
    if (!r || 100 - r.real <= 0) return false;
    return contexto === "sessao" ? r.esperado >= 3 : r.esperado >= 3 || r.eta === 0;
  }

  function textoRitmo(r, contexto, agoraMs, mostraFolga) {
    const n = Math.round(Math.abs(r.delta));
    const esquerda =
      n === 0 || r.estagio === "no-ritmo" ? "No ritmo" : r.delta >= 0 ? n + "% em déficit" : n + "% em reserva";
    let direita = "";
    if (r.duraAteZerar) {
      direita = "Dura até a renovação";
      // so o Codex da essa dica, e so com folga grande de verdade
      if (mostraFolga && r.delta < -15 && r.folgaVel >= 1.5) direita += " · folga de 1,5×";
    } else if (r.eta !== null) {
      const c = contagem(agoraMs + r.eta * 1000, agoraMs);
      if (contexto === "sessao") direita = c === "agora" ? "Esgotamento previsto agora" : "Esgotamento previsto em " + c;
      else direita = c === "agora" ? "Esgota agora" : "Esgota em " + c;
    }
    return direita ? esquerda + " · " + direita : esquerda;
  }

  function listraRitmo(r) {
    // a barra mostra o restante, entao a marca do esperado fica em 100 menos ele
    if (!r || r.estagio === "no-ritmo") return null;
    return { p: 100 - r.esperado, deficit: r.real > r.esperado };
  }

  function entalhes(limiares) {
    return limiares.filter((t) => t > 0 && t < 100);
  }

  function textoTokens(v) {
    const a = Math.abs(v);
    const sinal = v < 0 ? "-" : "";
    // sobe de unidade onde a de baixo arredondaria pra mil
    const unidades = [[999500000, 1e9, "B"], [999500, 1e6, "M"], [1000, 1e3, "K"]];
    for (const [limite, divisor, sufixo] of unidades) {
      if (a < limite) continue;
      const escala = a / divisor;
      const texto = escala >= 10 ? String(meioPar(escala)) : (meioPar(escala * 10) / 10).toFixed(1).replace(/\.0$/, "");
      return sinal + texto + sufixo;
    }
    return String(Math.round(v));
  }

  function comMilhar(inteiro) {
    return inteiro.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  function textoMoeda(v) {
    // sempre no formato americano, como o CodexBar: o preco da tabela e em dolar
    const partes = Math.abs(v).toFixed(2).split(".");
    return (v < 0 ? "-" : "") + "$" + comMilhar(partes[0]) + "." + partes[1];
  }

  function textoMoedaCompacta(v) {
    if (v !== 0 && Math.abs(v) < 1) return textoMoeda(v);
    return (v < 0 ? "-" : "") + "$" + comMilhar(Math.abs(v).toFixed(0));
  }

  function linhaCusto(rotulo, custo, tokens) {
    const valor = custo === null ? TRACO : textoMoeda(custo);
    return tokens === null ? rotulo + ": " + valor : rotulo + ": " + valor + " · " + textoTokens(tokens) + " tokens";
  }

  function marcasEixoY(max, metrica) {
    if (!(max > 0)) return [];
    const corte = metrica === "custo" ? 1 : 2;
    return max < corte ? [0, max] : [0, max / 2, max];
  }

  function geometriaBarras(valores, larguraPlot, alturaPlot) {
    /* Uma barra por dia, com 70% da faixa do dia. A tampa amarela e o topo do dia de pico, com
       5% da altura, so quando ha algum valor acima de zero. */
    const max = Math.max.apply(null, [0].concat(valores));
    const faixa = larguraPlot / Math.max(1, valores.length);
    const larg = faixa * 0.7;
    let pico = -1;
    if (max > 0) valores.forEach((v, i) => { if (pico < 0 || v > valores[pico]) pico = i; });
    return valores.map((v, i) => {
      const h = max > 0 ? (v / max) * alturaPlot : 0;
      return {
        x: i * faixa + (faixa - larg) / 2,
        larg: larg,
        y: alturaPlot - h,
        h: h,
        tampa: i === pico ? Math.min(h, 0.05 * alturaPlot) : 0,
      };
    });
  }

  function indiceNoGrafico(x, largura, n) {
    // com uma barra so, qualquer ponto do grafico escolhe ela
    if (!(n > 0)) return -1;
    if (n === 1 || !(largura > 0)) return n - 1;
    return prender(Math.floor((x / largura) * n), 0, n - 1);
  }

  function limitarLargura(valor) {
    if (typeof valor !== "number" && typeof valor !== "string") return LARGURA_PADRAO;
    const n = Number(valor);
    if (valor === "" || !Number.isFinite(n)) return LARGURA_PADRAO;
    return Math.round(prender(n, LARGURA_MINIMA, LARGURA_MAXIMA));
  }

  function meioCortado(texto, maximo) {
    // e-mail corta no meio, como no CodexBar: o comeco e o dominio e que dizem de quem e
    const t = String(texto || "");
    if (t.length <= maximo) return t;
    const cabeca = Math.ceil((maximo - 1) / 2);
    const cauda = Math.floor((maximo - 1) / 2);
    return t.slice(0, cabeca) + "…" + t.slice(t.length - cauda);
  }

  function textoPlano(plano) {
    return String(plano || "")
      .replace(/[_-]+/g, " ")
      .trim()
      .replace(/(^|\s)(\S)/g, (_, espaco, letra) => espaco + letra.toUpperCase());
  }

  function instante(iso) {
    if (iso === null || iso === undefined || iso === "") return null;
    const ms = typeof iso === "number" ? iso : Date.parse(iso);
    return Number.isFinite(ms) ? ms : null;
  }

  function esc(texto) {
    return escapar(texto === null || texto === undefined ? "" : texto);
  }

  function r2(v) {
    return Math.round(v * 100) / 100;
  }

  /* ------------------------------------------------------------- dados */

  function lerRelatorio(bruto) {
    let lido = bruto;
    if (typeof bruto === "string") {
      try {
        lido = JSON.parse(bruto);
      } catch (e) {
        return { geradoEm: null, coletando: false, contas: [], ilegivel: true };
      }
    }
    if (lido === null || lido === undefined) return { geradoEm: null, coletando: false, contas: [], ilegivel: false };
    if (typeof lido !== "object" || Array.isArray(lido)) {
      return { geradoEm: null, coletando: false, contas: [], ilegivel: true };
    }
    const contas = Array.isArray(lido.contas)
      ? lido.contas.filter((c) => c && typeof c === "object" && c.id)
      : [];
    return { geradoEm: lido.geradoEm || null, coletando: !!lido.coletando, contas: contas, ilegivel: false };
  }

  function provedorDe(ferramenta) {
    const conhecido = PROVEDORES[ferramenta];
    if (conhecido) return conhecido;
    const nome = String(ferramenta || "outro");
    return { nome: nome.charAt(0).toUpperCase() + nome.slice(1), marca: "var(--acento)", icone: ICONE_OUTRO };
  }

  function agrupar(contas) {
    const grupos = [];
    for (const conta of contas) {
      const ferramenta = String(conta.ferramenta || "outro");
      let grupo = grupos.find((g) => g.ferramenta === ferramenta);
      if (!grupo) {
        grupo = { ferramenta: ferramenta, contas: [] };
        grupos.push(grupo);
      }
      grupo.contas.push(conta);
    }
    return grupos;
  }

  function contaDoGrupo(grupo, vista) {
    const id = (vista.contas || {})[grupo.ferramenta];
    return grupo.contas.find((c) => c.id === id) || grupo.contas[0];
  }

  function sobreJanela(j) {
    return JANELAS[j.tipo] || { titulo: j.rotulo || j.tipo || "Janela", contexto: "semanal", minutos: null, ordem: 2 };
  }

  function janelasDe(conta) {
    const lista = Array.isArray(conta.janelas) ? conta.janelas.filter((j) => j && typeof j === "object") : [];
    return lista
      .map((j, i) => [j, i])
      .sort((a, b) => sobreJanela(a[0]).ordem - sobreJanela(b[0]).ordem || a[1] - b[1])
      .map((par) => par[0]);
  }

  function restanteDe(j) {
    const usado = numero(j && j.usado);
    return usado === null ? null : 100 - prender(usado, 0, 100);
  }

  function cotaDaAba(conta) {
    // a mini barra da aba e a da semana, que e a que dura; sem ela, a da sessao
    const janelas = janelasDe(conta);
    const semana = janelas.find((j) => j.tipo === "7d" && restanteDe(j) !== null);
    const sessao = janelas.find((j) => j.tipo === "5h" && restanteDe(j) !== null);
    const alvo = semana || sessao;
    return alvo ? restanteDe(alvo) : null;
  }

  function serieDe(conta) {
    return (Array.isArray(conta.serie) ? conta.serie : []).filter((d) => d && typeof d.dia === "string");
  }

  function temDado(conta) {
    const custo = conta.custo || {};
    const tokens = conta.tokens || {};
    const gasto = [custo.hoje, custo.dias30, tokens.hoje, tokens.dias30].some((v) => positivo(v) > 0);
    const dias = serieDe(conta).some((d) => positivo(d.custo) > 0 || positivo(d.tokens) > 0);
    return janelasDe(conta).length > 0 || gasto || dias || !!conta.bloco;
  }

  /* -------------------------------------------------------------- html */

  function htmlSemContas(estado, dados, agoraMs) {
    let legenda = "";
    if (estado === "coletando") legenda = '<span class="uso-legenda carregando">Atualizando…</span><i class="uso-giro"></i>';
    else if (estado === "ilegivel") legenda = '<span class="uso-legenda erro">Não deu pra ler o relatório de uso.</span>';
    else {
      const ms = instante(dados && dados.geradoEm);
      legenda = '<span class="uso-legenda">' + (ms === null ? "Ainda não buscado" : textoAtualizado(ms, agoraMs)) + "</span>";
    }
    let html =
      '<section class="uso-cartao uso-cartao-geral"><header class="uso-cabeca">' +
      '<div class="uso-cabeca-linha"><h3 class="uso-nome">Uso das contas</h3></div>' +
      '<div class="uso-cabeca-linha">' + legenda + "</div></header>";
    if (estado === "vazio") {
      html +=
        '<hr class="uso-separador uso-separador-cabeca"><p class="uso-sem-dado">Ainda sem uso</p>' +
        '<p class="uso-nota">As contas aparecem aqui depois do primeiro uso do Claude ou do Codex.</p>';
    }
    return html + "</section>";
  }

  function htmlProvedores(grupos, ativo, vista) {
    if (grupos.length < 2) return "";
    const abas = grupos.map((g) => {
      const prov = provedorDe(g.ferramenta);
      const escolhido = g === ativo;
      const cota = cotaDaAba(contaDoGrupo(g, vista));
      const mini =
        cota === null ? "" : '<i class="uso-provedor-cota"><b style="width:' + r2(larguraPreenchimento(cota)) + '%"></b></i>';
      return (
        '<button class="uso-provedor' + (escolhido ? " ativo" : "") + '" role="tab" aria-selected="' + escolhido + '"' +
        ' data-provedor="' + esc(g.ferramenta) + '" style="--marca:' + prov.marca + '" title="' + esc(prov.nome) + '">' +
        '<svg class="uso-provedor-icone" viewBox="0 0 100 100" aria-hidden="true">' + prov.icone + "</svg>" +
        '<span class="uso-provedor-nome">' + esc(prov.nome) + "</span>" + mini + "</button>"
      );
    });
    return '<nav class="uso-provedores" role="tablist">' + abas.join("") + "</nav>";
  }

  function htmlContas(grupo, ativa) {
    const n = grupo.contas.length;
    if (n < 2) return "";
    // ate tres cabem numa fileira; mais que isso, duas fileiras, como no seletor do CodexBar
    const colunas = n <= 3 ? n : Math.ceil(n / 2);
    const botoes = grupo.contas.map((c) => {
      const rotulo = c.nome || c.email || c.id;
      const curto = c.nome ? c.nome : meioCortado(rotulo, 24);
      const dica = c.email && c.nome ? c.nome + ", " + c.email : rotulo;
      const escolhida = c === ativa;
      return (
        '<button class="uso-seletor-conta' + (escolhida ? " ativo" : "") + '" role="tab" aria-selected="' + escolhida + '"' +
        ' data-ferramenta="' + esc(grupo.ferramenta) + '" data-escolher-conta="' + esc(c.id) + '" title="' + esc(dica) + '">' +
        esc(curto) + "</button>"
      );
    });
    return '<div class="uso-seletor-contas" role="tablist" style="--colunas:' + colunas + '">' + botoes.join("") + "</div>";
  }

  function legendaDe(conta, coletando, agoraMs) {
    if (conta.erro) return { classe: "erro", texto: String(conta.erro) };
    if (coletando) return { classe: "carregando", texto: "Atualizando…" };
    const ms = instante(conta.atualizadoEm);
    return { classe: "", texto: ms === null ? "Ainda não buscado" : textoAtualizado(ms, agoraMs) };
  }

  function htmlCabeca(conta, prov, coletando, agoraMs, vista) {
    const legenda = legendaDe(conta, coletando, agoraMs);
    // a conta de sempre do Codex se chama Codex: repetir o nome do provedor ao lado nao diz nada
    const nome = String(conta.nome || "").toLowerCase() === prov.nome.toLowerCase() ? "" : conta.nome || "";
    const quem = conta.email ? meioCortado(conta.email, 30) : nome;
    const dica = [conta.nome, conta.email].filter(Boolean).join(", ");
    const plano = textoPlano(conta.plano);
    let html = '<header class="uso-cabeca"><div class="uso-cabeca-linha"><h3 class="uso-nome">' + esc(prov.nome) + "</h3>";
    if (quem) html += '<span class="uso-email" title="' + esc(dica) + '">' + esc(quem) + "</span>";
    html += '</div><div class="uso-cabeca-linha' + (legenda.classe === "erro" ? " com-erro" : "") + '">';
    html +=
      '<span class="uso-legenda' + (legenda.classe ? " " + legenda.classe : "") + '"' +
      (legenda.classe === "erro" ? ' title="' + esc(legenda.texto) + '"' : "") + ">" + esc(legenda.texto) + "</span>";
    if (legenda.classe === "erro") {
      const copiado = vista.copiado === conta.id;
      html +=
        '<button class="uso-copiar' + (copiado ? " copiado" : "") + '" data-copiar title="Copiar o erro">' +
        (copiado ? ICONE_COPIADO : ICONE_COPIAR) + "</button>";
    }
    if (plano) html += '<span class="uso-plano">' + esc(plano) + "</span>";
    return html + "</div></header>";
  }

  function htmlJanela(j, conta, agoraMs) {
    const sobre = sobreJanela(j);
    const zeraMs = instante(j.zeraEm);
    const zera = zeraMs === null ? "" : textoZera(zeraMs, agoraMs);
    const selo = j.origem === "estimada" ? '<span class="uso-selo" title="' + DICA_ESTIMADA + '">estimado</span>' : "";
    const titulo = '<span class="uso-janela-titulo">';
    const usado = numero(j.usado);
    if (usado === null) {
      return (
        '<div class="uso-janela uso-janela-status" data-tipo="' + esc(j.tipo) + '">' +
        '<div class="uso-janela-topo">' + titulo + esc(sobre.titulo) + "</span>" + selo + "</div>" +
        '<p class="uso-janela-meta">Indisponível' + (zera ? " · " + zera : "") + "</p></div>"
      );
    }
    const resta = 100 - prender(usado, 0, 100);
    const r = ritmo({ usado: usado, janelaMin: numero(j.duracaoMin), zeraEmMs: zeraMs }, agoraMs, sobre.minutos);
    const comRitmo = ritmoVisivel(r, sobre.contexto);
    const listra = comRitmo ? listraRitmo(r) : null;
    let trilho =
      '<div class="uso-trilho" role="meter" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + Math.round(resta) + '"' +
      ' aria-label="' + esc(sobre.titulo) + '"><b class="uso-trilho-preenche" style="width:' + r2(larguraPreenchimento(resta)) + '%"></b>';
    for (const p of entalhes(LIMIARES)) trilho += '<i class="uso-trilho-marca" style="--p:' + p + '"></i>';
    if (listra) trilho += '<i class="uso-trilho-ritmo ' + (listra.deficit ? "deficit" : "reserva") + '" style="--p:' + r2(listra.p) + '"></i>';
    trilho += "</div>";
    return (
      '<div class="uso-janela" data-tipo="' + esc(j.tipo) + '"><div class="uso-janela-topo">' +
      titulo + esc(sobre.titulo) + " " + textoPercentual(resta, "restante") + "</span>" + selo +
      (zera ? '<span class="uso-janela-zera">' + zera + "</span>" : "") + "</div>" + trilho +
      (comRitmo ? '<p class="uso-janela-meta">' + textoRitmo(r, sobre.contexto, agoraMs, conta.ferramenta === "codex") + "</p>" : "") +
      "</div>"
    );
  }

  function htmlCusto(conta, agoraMs, coletando) {
    const custo = conta.custo || {};
    const tokens = conta.tokens || {};
    const linha = (texto, extra) => '<p class="uso-custo-linha' + (extra || "") + '" title="' + esc(texto) + '">' + esc(texto) + "</p>";
    let html =
      '<div class="uso-custo"><div class="uso-custo-topo"><span class="uso-bloco-titulo">Custo</span>' +
      (coletando ? '<i class="uso-giro"></i>' : "") + "</div>";
    html += linha(linhaCusto("Hoje", numero(custo.hoje), numero(tokens.hoje)));
    html += linha(linhaCusto("Últimos 30 dias", numero(custo.dias30), numero(tokens.dias30)));
    const bloco = conta.bloco && typeof conta.bloco === "object" ? conta.bloco : null;
    const fimMs = bloco ? instante(bloco.fim) : null;
    // bloco que ja acabou nao e mais o atual; mostrar o custo dele ali enganaria
    if (bloco && (fimMs === null || fimMs > agoraMs)) {
      html += linha(linhaCusto(bloco.estimado ? "Bloco estimado" : "Bloco atual", numero(bloco.custo), numero(bloco.tokens)));
      const projecao = numero(bloco.projecaoCusto);
      if (projecao !== null) {
        html += linha("Projeção do bloco: " + textoMoeda(projecao) + (fimMs === null ? "" : " até " + horaCurta(fimMs)), " uso-sec");
      }
    }
    if (custo.semPreco) html += '<p class="uso-custo-aviso">' + AVISO_SEM_PRECO + "</p>";
    if (custo.aproximado) html += '<p class="uso-custo-aviso">' + AVISO_APROXIMADO + "</p>";
    return html + '<p class="uso-custo-dica">' + AVISO_ESTIMATIVA + "</p></div>";
  }

  function htmlGrafico(conta, vista, coletando) {
    const serie = serieDe(conta);
    const custos = serie.map((d) => positivo(d.custo));
    const tokens = serie.map((d) => positivo(d.tokens));
    const temCusto = custos.some((v) => v > 0);
    const temTokens = tokens.some((v) => v > 0);
    if (!temCusto && !temTokens) return '<div class="uso-grafico"><p class="uso-grafico-vazio">Nenhum dado disponível</p></div>';

    // o Codex abre em tokens, como no CodexBar; metrica sem valor nenhum cai na outra
    let metrica = (vista.metricas || {})[conta.id];
    if (metrica !== "custo" && metrica !== "tokens") metrica = conta.ferramenta === "codex" ? "tokens" : "custo";
    if (metrica === "custo" && !temCusto) metrica = "tokens";
    if (metrica === "tokens" && !temTokens) metrica = "custo";
    const valores = metrica === "custo" ? custos : tokens;
    const max = Math.max.apply(null, valores);
    const n = serie.length;
    let escolhido = serie.findIndex((d) => d.dia === (vista.dias || {})[conta.id]);
    if (escolhido < 0) escolhido = n - 1;

    /* As barras sao desenhadas em 1000 unidades de largura e esticam com o painel; a altura e em
       pixel de verdade. Os rotulos ficam fora do SVG, em HTML, pra letra nao esticar junto. */
    const LARGURA = 1000;
    const TOPO = 6;
    const ALTURA = 112;
    const barras = geometriaBarras(valores, LARGURA, ALTURA);
    const nome = (v) => (metrica === "custo" ? textoMoedaCompacta(v) : textoTokens(Math.round(v)));
    // as marcas sao 0, meio e topo, igualmente espacadas: a coluna distribui de cima pra baixo
    const eixoY = marcasEixoY(max, metrica)
      .reverse()
      .map((v) => "<span>" + esc(nome(v)) + "</span>")
      .join("");
    const sel = barras[escolhido];
    let svg =
      '<svg class="uso-grafico-area" viewBox="0 0 ' + LARGURA + " " + (ALTURA + TOPO) + '" preserveAspectRatio="none"' +
      ' data-n="' + n + '" aria-hidden="true">' +
      '<rect class="faixa" x="' + r2(sel.x) + '" y="' + TOPO + '" width="' + r2(sel.larg) + '" height="' + ALTURA + '"/>';
    for (const b of barras) {
      if (b.h < 0.25) continue;
      svg += '<rect class="barra" x="' + r2(b.x) + '" y="' + r2(TOPO + b.y) + '" width="' + r2(b.larg) + '" height="' + r2(b.h) + '"/>';
    }
    for (const b of barras) {
      if (b.tampa > 0) {
        svg += '<rect class="pico" x="' + r2(b.x) + '" y="' + r2(TOPO + b.y) + '" width="' + r2(b.larg) + '" height="' + r2(b.tampa) + '"/>';
      }
    }
    svg += "</svg>";
    const eixoX =
      n === 1
        ? '<span class="so-um">' + esc(diaDaSerie(serie[0].dia)) + "</span>"
        : "<span>" + esc(diaDaSerie(serie[0].dia)) + "</span><span>" + esc(diaDaSerie(serie[n - 1].dia)) + "</span>";

    let rodape = coletando ? '<span class="uso-grafico-lendo">Atualizando</span>' : "";
    if (temCusto && temTokens) {
      const botao = (m, rotulo) =>
        "<button" + (metrica === m ? ' class="ativo"' : "") + ' data-metrica="' + m + '" aria-selected="' + (metrica === m) + '">' +
        rotulo + "</button>";
      rodape += '<div class="uso-grafico-modo" role="tablist">' + botao("tokens", "Token") + botao("custo", "Custo") + "</div>";
    }

    const dia = serie[escolhido];
    const modelos = Object.keys(dia.porModelo && typeof dia.porModelo === "object" ? dia.porModelo : {})
      .map((id) => ({ id: id, custo: positivo(dia.porModelo[id]) }))
      .sort((a, b) => b.custo - a.custo || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
    let detalhe =
      '<p class="uso-grafico-dia">' +
      esc(diaDaSerie(dia.dia) + ": " + textoMoeda(positivo(dia.custo)) + " · " + textoTokens(positivo(dia.tokens)) + " tokens") +
      "</p>";
    if (modelos.length) {
      detalhe +=
        '<ul class="uso-grafico-modelos">' +
        modelos
          .map(
            (m, k) =>
              '<li class="uso-grafico-modelo" style="--a:' + Math.max(0.3, 0.75 - 0.12 * k).toFixed(2) + '">' +
              '<i class="uso-grafico-acento"></i><div><b>' + esc(nomeDeModelo(m.id)) + "</b><span>" + textoMoeda(m.custo) +
              "</span></div></li>"
          )
          .join("") +
        "</ul>";
      if (modelos.length > 4) detalhe += '<p class="uso-grafico-mais">Role para ver mais modelos</p>';
    }

    const soma = valores.reduce((a, v) => a + v, 0);
    // com um dia so, ele nao e necessariamente hoje: o total diz qual e
    const periodo = n === 1 ? "em " + diaDaSerie(serie[0].dia) : "dos últimos " + n + " dias";
    const total =
      metrica === "custo"
        ? "Total est. " + periodo + ": " + textoMoeda(soma)
        : "Total " + periodo + ": " + textoTokens(Math.round(soma)) + " tokens";

    return (
      '<div class="uso-grafico" data-metrica-atual="' + metrica + '">' +
      '<div class="uso-grafico-quadro"><div class="uso-grafico-eixo-y">' + eixoY + "</div>" + svg +
      '<div class="uso-grafico-eixo-x">' + eixoX + "</div></div>" +
      (rodape ? '<div class="uso-grafico-rodape">' + rodape + "</div>" : "") +
      '<div class="uso-grafico-detalhe">' + detalhe + "</div>" +
      '<p class="uso-grafico-total">' + total + "</p></div>"
    );
  }

  function htmlCartao(conta, grupo, agoraMs, coletando, vista) {
    const prov = provedorDe(grupo.ferramenta);
    let html =
      '<section class="uso-cartao" style="--marca:' + prov.marca + '" data-conta="' + esc(conta.id) + '">' +
      htmlCabeca(conta, prov, coletando, agoraMs, vista);
    if (!temDado(conta)) {
      // so o erro: cabecalho com a legenda vermelha e o botao de copiar, sem corpo
      if (conta.erro) return html + "</section>";
      return html + '<hr class="uso-separador uso-separador-cabeca"><p class="uso-sem-dado">Ainda sem uso</p></section>';
    }
    const janelas = janelasDe(conta);
    html += '<hr class="uso-separador uso-separador-cabeca' + (janelas.length ? " com-janelas" : "") + '"><div class="uso-corpo">';
    html += janelas.length
      ? '<div class="uso-janelas">' + janelas.map((j) => htmlJanela(j, conta, agoraMs)).join("") + "</div>"
      : '<p class="uso-sem-dado">Limites indisponíveis</p>';
    html += '<hr class="uso-separador">' + htmlCusto(conta, agoraMs, coletando) + htmlGrafico(conta, vista, coletando);
    return html + "</div></section>";
  }

  function html(dados, vista, agoraMs) {
    const v = vista || {};
    if (!dados) return htmlSemContas("coletando", null, agoraMs);
    if (dados.ilegivel) return htmlSemContas("ilegivel", dados, agoraMs);
    if (!dados.contas.length) return htmlSemContas(dados.coletando ? "coletando" : "vazio", dados, agoraMs);
    const grupos = agrupar(dados.contas);
    const grupo = grupos.find((g) => g.ferramenta === v.provedor) || grupos[0];
    const conta = contaDoGrupo(grupo, v);
    let saida = htmlProvedores(grupos, grupo, v);
    if (saida) saida += '<hr class="uso-separador">';
    const contas = htmlContas(grupo, conta);
    if (contas) saida += contas + '<hr class="uso-separador">';
    return saida + htmlCartao(conta, grupo, agoraMs, dados.coletando, v);
  }

  /* -------------------------------------------------------------- tela */

  let painel = null;
  let puxador = null;
  let botao = null;
  let dados = null;
  let larguraAtual = LARGURA_PADRAO;
  let ultimoHtml = "";
  let ultimoPedido = 0;
  const vista = { provedor: "", contas: {}, metricas: {}, dias: {}, copiado: "" };

  function visivel() {
    return !!painel && !painel.hidden && document.hidden !== true;
  }

  function gravar(parcial) {
    if (bridge) bridge.saveConfig(JSON.stringify(parcial), () => {});
  }

  function reencaixar() {
    // a coluna dos terminais mudou de largura: na grade as colunas sao refeitas antes de cada
    // terminal se remedir, e fora dela so a aba a vista precisa, igual ao puxador da lateral
    if (emGrade) reencaixarGrade();
    else if (abaAtiva) abaAtiva.ajustar();
  }

  function aplicarAberto(aberto) {
    painel.hidden = !aberto;
    if (puxador) puxador.hidden = !aberto;
    if (botao) {
      botao.classList.toggle("ativo", aberto);
      botao.setAttribute("aria-pressed", aberto ? "true" : "false");
    }
  }

  function aplicarLargura(largura) {
    larguraAtual = largura;
    painel.style.setProperty("--largura-uso", largura + "px");
  }

  function pintar() {
    if (!painel) return;
    const texto = html(dados, vista, Date.now());
    // o relogio de 30 s quase sempre acha o mesmo texto: sem mudanca, a pagina nem e tocada
    if (texto === ultimoHtml) return;
    ultimoHtml = texto;
    const rolagem = painel.scrollTop;
    painel.innerHTML = texto;
    painel.scrollTop = rolagem;
  }

  function receber(json) {
    dados = lerRelatorio(json);
    if (visivel()) pintar();
  }

  function pedir() {
    ultimoPedido = Date.now();
    if (!bridge || typeof bridge.getPainelUso !== "function") {
      // app sem o relatorio: o painel diz que nao ha uso, em vez de girar pra sempre
      if (!dados) receber(null);
      return;
    }
    // o app devolve na hora o ultimo relatorio pronto e coleta o novo numa thread; o novo chega
    // pelo painelUsoPronto, entao nada disto espera disco na thread da tela
    bridge.getPainelUso(receber);
  }

  function alternar(forcar) {
    if (!painel) return;
    const aberto = typeof forcar === "boolean" ? forcar : painel.hidden;
    cfg.painelUso = aberto;
    aplicarAberto(aberto);
    gravar({ painelUso: aberto });
    reencaixar();
    if (aberto) {
      pintar();
      pedir();
    }
  }

  function aplicar() {
    // a mudanca veio das Configuracoes; so mexe no que mudou, pra nao remedir terminal a toa
    if (!painel) return;
    const aberto = cfg.painelUso !== false;
    const largura = limitarLargura(cfg.larguraPainelUso);
    const mudouAberto = aberto === painel.hidden;
    if (!mudouAberto && largura === larguraAtual) return;
    aplicarAberto(aberto);
    aplicarLargura(largura);
    reencaixar();
    if (aberto && mudouAberto) {
      pintar();
      pedir();
    }
  }

  function arrastarAte(x) {
    // o painel mora na borda direita da janela e cresce pra esquerda, atras do ponteiro
    const largura = limitarLargura(painel.getBoundingClientRect().right - x);
    if (largura === larguraAtual) return;
    cfg.larguraPainelUso = largura;
    aplicarLargura(largura);
  }

  function ligarPuxador() {
    if (!puxador) return;
    let arrastando = false;
    let atraso = null;
    puxador.addEventListener("mousedown", (ev) => {
      if (ev.button) return;
      ev.preventDefault();
      arrastando = true;
      puxador.classList.add("ativo");
      document.body.classList.add("puxando-largura");
    });
    window.addEventListener("mousemove", (ev) => {
      if (!arrastando) return;
      arrastarAte(ev.clientX);
      clearTimeout(atraso);
      atraso = setTimeout(reencaixar, ESPERA_DO_ENCAIXE_MS);
    });
    window.addEventListener("mouseup", () => {
      if (!arrastando) return;
      arrastando = false;
      clearTimeout(atraso);
      puxador.classList.remove("ativo");
      document.body.classList.remove("puxando-largura");
      gravar({ larguraPainelUso: larguraAtual });
      reencaixar();
    });
  }

  function alvoDe(ev, seletor) {
    const t = ev && ev.target;
    return t && typeof t.closest === "function" ? t.closest(seletor) : null;
  }

  function contaPorId(id) {
    return ((dados && dados.contas) || []).find((c) => c.id === id) || null;
  }

  function copiarErro(id) {
    const conta = contaPorId(id);
    if (!conta || !conta.erro) return;
    copiarComAviso(String(conta.erro), "Erro copiado");
    // o icone vira um visto por 0,9 s, como no CodexBar
    vista.copiado = id;
    setTimeout(() => {
      vista.copiado = "";
      pintar();
    }, 900);
  }

  function aoClicar(ev) {
    const alvo = alvoDe(ev, "[data-provedor],[data-escolher-conta],[data-metrica],[data-copiar]");
    if (!alvo) return;
    if (alvo.hasAttribute("data-provedor")) {
      vista.provedor = alvo.getAttribute("data-provedor");
    } else if (alvo.hasAttribute("data-escolher-conta")) {
      vista.contas[alvo.getAttribute("data-ferramenta")] = alvo.getAttribute("data-escolher-conta");
    } else {
      const cartao = alvo.closest("[data-conta]");
      const id = cartao ? cartao.getAttribute("data-conta") : "";
      if (alvo.hasAttribute("data-metrica")) vista.metricas[id] = alvo.getAttribute("data-metrica");
      else copiarErro(id);
    }
    pintar();
  }

  function aoMover(ev) {
    // a barra debaixo do ponteiro vira a escolhida e continua escolhida quando ele sai
    const area = alvoDe(ev, ".uso-grafico-area");
    if (!area) return;
    const cartao = area.closest("[data-conta]");
    const conta = cartao ? contaPorId(cartao.getAttribute("data-conta")) : null;
    if (!conta) return;
    const serie = serieDe(conta);
    const caixa = area.getBoundingClientRect();
    const i = indiceNoGrafico(ev.clientX - caixa.left, caixa.width, serie.length);
    if (i < 0 || vista.dias[conta.id] === serie[i].dia) return;
    vista.dias[conta.id] = serie[i].dia;
    pintar();
  }

  function segurarFoco(ev) {
    // clicar no painel nao tira o foco do terminal: quem clica numa aba de provedor volta a
    // digitar sem clicar no shell de novo. A barra de rolagem do painel fica livre.
    if (ev.target !== painel) ev.preventDefault();
  }

  function ligar() {
    /* Chamado uma vez, quando a configuracao chega e antes de qualquer aba abrir: o terminal ja
       nasce na largura certa, sem remedir logo depois. */
    if (painel) return;
    painel = document.getElementById("uso-lateral");
    puxador = document.getElementById("puxador-uso");
    botao = document.getElementById("alternar-uso");
    if (!painel) return;
    aplicarAberto(cfg.painelUso !== false);
    aplicarLargura(limitarLargura(cfg.larguraPainelUso));
    if (botao) {
      botao.addEventListener("mousedown", (ev) => ev.preventDefault());
      botao.addEventListener("click", () => alternar());
    }
    painel.addEventListener("mousedown", segurarFoco);
    painel.addEventListener("click", aoClicar);
    painel.addEventListener("mousemove", aoMover);
    ligarPuxador();
    if (bridge && bridge.painelUsoPronto) bridge.painelUsoPronto.connect(receber);
    document.addEventListener("visibilitychange", () => {
      if (!visivel()) return;
      pintar();
      if (Date.now() - ultimoPedido >= PEDIR_A_CADA_MS) pedir();
    });
    setInterval(() => {
      if (visivel()) pedir();
    }, PEDIR_A_CADA_MS);
    setInterval(() => {
      if (visivel()) pintar();
    }, REDESENHAR_A_CADA_MS);
    pintar();
    if (visivel()) pedir();
  }

  return {
    ligar: ligar,
    aplicar: aplicar,
    alternar: alternar,
    receber: receber,
    lerRelatorio: lerRelatorio,
    html: html,
    limitarLargura: limitarLargura,
    arrastarAte: arrastarAte,
    regras: {
      textoPercentual: textoPercentual,
      larguraPreenchimento: larguraPreenchimento,
      contagem: contagem,
      textoZera: textoZera,
      textoAtualizado: textoAtualizado,
      ritmo: ritmo,
      ritmoVisivel: ritmoVisivel,
      textoRitmo: textoRitmo,
      listraRitmo: listraRitmo,
      textoTokens: textoTokens,
      textoMoeda: textoMoeda,
      textoMoedaCompacta: textoMoedaCompacta,
      linhaCusto: linhaCusto,
      marcasEixoY: marcasEixoY,
      geometriaBarras: geometriaBarras,
      indiceNoGrafico: indiceNoGrafico,
      meioCortado: meioCortado,
      textoPlano: textoPlano,
      diaDaSerie: diaDaSerie,
    },
  };
})();
