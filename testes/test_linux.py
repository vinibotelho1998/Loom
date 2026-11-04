"""O Loom no Linux: terminal de verdade, trava, atalho da status line e o que muda de sistema.

Os shells sobem num PTY sem janela nenhuma, com um rc vazio, e os dados ficam em pasta temporaria.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from base import ComPastaPropria, fora_do_windows  # noqa: E402

from loom import bridge as mod_bridge  # noqa: E402
from loom import caminhos, config, instancia, nucleo, perfis, pty_session, queda, shells  # noqa: E402

if sys.platform != "win32":
    from loom.pty_posix import PtyProcess

UI = Path(__file__).resolve().parent.parent / "ui"
AMBIENTE = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "TERM": "xterm-256color", "HOME": tempfile.gettempdir(), "PS1": "$ "}


def ler_ate(proc, marca: str, prazo: float = 8.0) -> str:
    """Le o terminal numa thread propria ate a marca aparecer, ou devolve o que veio ate o prazo."""
    import threading

    achado = []

    def bombear():
        try:
            while marca not in "".join(achado):
                achado.append(proc.read(65536))
        except EOFError:
            pass

    linha = threading.Thread(target=bombear, daemon=True)
    linha.start()
    linha.join(prazo)
    return "".join(achado)


def esperar(condicao, prazo: float = 8.0) -> bool:
    fim = time.monotonic() + prazo
    while time.monotonic() < fim:
        if condicao():
            return True
        time.sleep(0.02)
    return condicao()


@fora_do_windows
class TerminalDeVerdade(unittest.TestCase):
    def subir(self, *argv, dimensions=(24, 80)):
        proc = PtyProcess.spawn(list(argv), cwd=tempfile.gettempdir(), env=AMBIENTE, dimensions=dimensions)
        self.addCleanup(proc.close)
        return proc

    def bash(self, comando: str, **kwargs):
        return self.subir("/bin/bash", "--norc", "--noprofile", "-c", comando, **kwargs)

    def test_saida_chega_e_o_fim_vira_eoferror_com_o_codigo(self):
        proc = self.bash("echo ola; exit 7")
        self.assertIn("ola", ler_ate(proc, "\0nunca"))
        self.assertTrue(esperar(lambda: not proc.isalive()))
        self.assertEqual(proc.exitstatus, 7)
        with self.assertRaises(EOFError):
            proc.read()

    def test_o_que_se_digita_chega_no_programa(self):
        proc = self.bash("read linha; echo recebi-$linha")
        proc.write("acentuação\n")
        self.assertIn("recebi-acentuação", ler_ate(proc, "recebi-acentuação"))

    def test_caractere_partido_entre_duas_leituras_nao_vira_lixo(self):
        proc = self.bash("printf 'ã'; sleep 0.2; printf 'fim\\n'; sleep 0.2")
        recebido = ""
        while "fim" not in recebido:
            recebido += proc.read(1)
        self.assertIn("ãfim", recebido)
        self.assertNotIn("�", recebido)

    def test_medida_inicial_e_redimensionar_chegam_no_programa(self):
        proc = self.bash("stty size; read x; stty size", dimensions=(33, 101))
        self.assertIn("33 101", ler_ate(proc, "33 101"))
        proc.setwinsize(40, 132)
        proc.write("\n")
        self.assertIn("40 132", ler_ate(proc, "40 132"))

    def test_o_shell_e_dono_do_terminal(self):
        """O bash pega o terminal sozinho, e o que nao e shell nao: o sudo da aba de adm pede a senha
        pelo /dev/tty, e sem o terminal de controle morria ali."""
        proc = self.subir(shutil.which("cat"), "/dev/tty")
        time.sleep(0.4)
        self.assertTrue(proc.isalive(), "abrir o /dev/tty falhou: o processo nao e dono do terminal")

    def test_o_shell_tem_terminal_de_controle_e_ctrl_c_mata_o_programa(self):
        """Sem o terminal de controle nao ha Ctrl+C, e o sinal nunca chega ao programa da frente."""
        proc = self.bash("trap 'echo pegou-o-sinal; exit 3' INT; echo pronto; while :; do sleep 0.05; done")
        ler_ate(proc, "pronto")
        proc.write("\x03")
        self.assertIn("pegou-o-sinal", ler_ate(proc, "pegou-o-sinal"))
        self.assertTrue(esperar(lambda: not proc.isalive()))
        self.assertEqual(proc.exitstatus, 3)

    def test_morto_por_sinal_vira_128_mais_o_sinal(self):
        proc = self.bash("kill -KILL $$")
        self.assertTrue(esperar(lambda: not proc.isalive()))
        self.assertEqual(proc.exitstatus, 128 + signal.SIGKILL)

    def test_programa_em_segundo_plano_nao_segura_a_aba_depois_do_shell_sair(self):
        """O mestre so da fim quando todos largam o escravo: sem vigiar o shell, a aba ficava aberta."""
        proc = self.bash("sleep 30 & disown; echo saindo")
        ler_ate(proc, "saindo")
        inicio = time.monotonic()
        with self.assertRaises(EOFError):
            while True:
                proc.read()
        self.assertLess(time.monotonic() - inicio, 5)

    def test_fechar_derruba_o_programa_da_frente_junto_com_o_shell(self):
        """O Claude roda em outro grupo que o do shell, e nao podia ficar vivo sem tela."""
        proc = self.subir("/bin/bash", "--norc", "--noprofile", "-i")
        marca = Path(tempfile.mkdtemp(prefix="loom-pid-")) / "pid"
        self.addCleanup(shutil.rmtree, marca.parent, True)
        proc.write(f"bash --norc --noprofile -c 'echo $$ > {marca}; exec sleep 30'\n")
        self.assertTrue(esperar(lambda: marca.exists() and marca.read_text().strip()))
        filho = int(marca.read_text())
        proc.close()
        self.assertTrue(esperar(lambda: not proc.isalive()))
        self.assertTrue(esperar(lambda: not pty_session.tem_filhos(proc.pid) and not Path(f"/proc/{filho}/stat").exists()))

    def test_fechar_aba_sem_programa_na_frente_nao_sinaliza_o_proprio_loom(self):
        """O terminal responde grupo 0, e killpg de 0 e o grupo de quem chama: derrubava o Loom."""
        sinais = []
        with unittest.mock.patch.object(os, "killpg", side_effect=lambda g, s: sinais.append((g, s))):
            PtyProcess._sinalizar({0, 1, os.getpgrp()}, signal.SIGHUP)
        self.assertEqual(sinais, [])
        self.assertFalse(PtyProcess._grupo_vivo(0))

    def test_programa_que_trata_o_hangup_ganha_tempo_de_gravar(self):
        """O shell sai na hora; o SIGKILL logo em seguida tirava do Claude o tempo de gravar."""
        pasta = Path(tempfile.mkdtemp(prefix="loom-hup-"))
        self.addCleanup(shutil.rmtree, pasta, True)
        marca = pasta / "gravou"
        proc = self.subir("/bin/bash", "--norc", "--noprofile", "-i")
        script = f"trap 'sleep 0.4; echo ok > {marca}; exit 0' HUP; echo pronto; while :; do sleep 0.05; done"
        proc.write(f"bash --norc --noprofile -c \"{script}\"\n")
        ler_ate(proc, "pronto\r")
        time.sleep(0.1)
        proc.close()
        self.assertTrue(esperar(lambda: marca.exists(), prazo=3), "o programa morreu antes de gravar")

    def test_fechar_acorda_quem_esta_lendo(self):
        import threading

        proc = self.subir("/bin/bash", "--norc", "--noprofile", "-i")
        terminou = threading.Event()

        def ler():
            try:
                while True:
                    proc.read()
            except EOFError:
                terminou.set()

        threading.Thread(target=ler, daemon=True).start()
        time.sleep(0.1)
        proc.close()
        self.assertTrue(terminou.wait(3), "a leitura ficou presa com o terminal fechado")

    def test_colar_muito_num_programa_que_nao_le_nao_trava_quem_escreve(self):
        # em modo de linha o kernel descarta o excesso e nunca bloqueia; o Claude roda em modo raw
        proc = self.bash("stty raw -echo; echo pronto; sleep 5")
        ler_ate(proc, "pronto")
        inicio = time.monotonic()
        proc.write("x" * 2_000_000)
        self.assertLess(time.monotonic() - inicio, 0.5, "a escrita travou a thread de quem chamou")

    def test_shell_que_nao_existe_e_oserror(self):
        with self.assertRaises(OSError):
            PtyProcess.spawn(["/nao/existe"], env=AMBIENTE)

    def test_descritor_do_terminal_nao_vaza_pro_filho(self):
        """Com o mestre herdado, o filho seguraria o terminal aberto depois de a aba fechar."""
        proc = self.bash("ls -l /proc/self/fd | grep -c ptmx; echo fim")
        saida = ler_ate(proc, "fim")
        self.assertEqual(saida.split()[0], "0", saida)


@fora_do_windows
class SessaoNoLinux(unittest.TestCase):
    def test_filhos_da_aba_saem_do_proc(self):
        proc = subprocess.Popen(["/bin/sh", "-c", "sleep 30 & wait"])
        self.addCleanup(proc.kill)
        self.addCleanup(proc.wait)
        self.assertTrue(esperar(lambda: pty_session.tem_filhos(proc.pid)))
        sozinho = subprocess.Popen(["sleep", "30"])
        self.addCleanup(sozinho.kill)
        self.addCleanup(sozinho.wait)
        self.assertFalse(pty_session.tem_filhos(sozinho.pid))

    def test_linha_do_proc_com_nome_cheio_de_parenteses(self):
        self.assertEqual(pty_session._linha_do_proc("12 (a b) (c)) S 7 12 12 0"), (12, 7, "a b) (c)"))

    def test_zumbi_nao_conta_como_programa_rodando(self):
        self.assertIsNone(pty_session._linha_do_proc("12 (claude) Z 7 12 12 0"))

    def test_exe_congelado_devolve_o_ld_library_path_de_antes(self):
        """A _internal do app vinha antes do sistema pra todo programa rodado na aba."""
        fora = {"LD_LIBRARY_PATH": "/app/_internal", "LD_LIBRARY_PATH_ORIG": "/minha/lib", "PATH": "/app:/usr/bin"}
        with (
            unittest.mock.patch.dict(os.environ, fora, clear=True),
            unittest.mock.patch.object(sys, "frozen", True, create=True),
            unittest.mock.patch.object(sys, "executable", "/app/Loom"),
        ):
            limpo = pty_session.ambiente_limpo()
        self.assertEqual(limpo["LD_LIBRARY_PATH"], "/minha/lib")
        self.assertNotIn("LD_LIBRARY_PATH_ORIG", limpo)
        self.assertEqual(limpo["PATH"], "/usr/bin")

    def test_exe_congelado_sem_variavel_de_antes_nao_deixa_nenhuma(self):
        fora = {"LD_LIBRARY_PATH": "/app/_internal", "PATH": "/usr/bin"}
        with (
            unittest.mock.patch.dict(os.environ, fora, clear=True),
            unittest.mock.patch.object(sys, "frozen", True, create=True),
            unittest.mock.patch.object(sys, "executable", "/app/Loom"),
        ):
            self.assertNotIn("LD_LIBRARY_PATH", pty_session.ambiente_limpo())

    def test_rodando_do_codigo_nao_mexe_no_ld_library_path(self):
        with unittest.mock.patch.dict(os.environ, {"LD_LIBRARY_PATH": "/minha/lib"}, clear=True):
            self.assertEqual(pty_session.ambiente_limpo()["LD_LIBRARY_PATH"], "/minha/lib")

    def test_marca_de_outro_terminal_nao_passa_pra_aba(self):
        with unittest.mock.patch.dict(os.environ, {"TERM_PROGRAM": "ghostty", "GHOSTTY_X": "1", "KONSOLE_VERSION": "2", "HOME": "/h"}, clear=True):
            limpo = pty_session.ambiente_limpo()
        self.assertEqual(limpo, {"HOME": "/h"})

    def test_shell_padrao_e_o_da_pessoa(self):
        with unittest.mock.patch.dict(os.environ, {"SHELL": "/usr/bin/fish"}):
            self.assertEqual(pty_session.default_shell(), ["/usr/bin/fish"])


@fora_do_windows
class ShellsNoLinux(unittest.TestCase):
    def test_lista_so_o_que_existe_e_o_shell_estranho_da_pessoa_entra(self):
        mapa = {"bash": "/usr/bin/bash", "fish": "/usr/bin/fish"}
        with (
            unittest.mock.patch.object(shells.shutil, "which", side_effect=mapa.get),
            unittest.mock.patch.dict(os.environ, {"SHELL": "/opt/elvish"}),
            unittest.mock.patch.object(shells.os.path, "exists", return_value=True),
        ):
            achados = shells.descobrir()
        self.assertEqual([s.id for s in achados], ["elvish", "bash", "fish"])

    def test_padrao_e_o_shell_da_pessoa(self):
        lista = [shells.Shell("bash", "Bash", "/b"), shells.Shell("zsh", "Zsh", "/z")]
        with unittest.mock.patch.dict(os.environ, {"SHELL": "/usr/bin/zsh"}):
            self.assertEqual(shells.padrao(lista).id, "zsh")
        with unittest.mock.patch.dict(os.environ, {"SHELL": "/usr/bin/desconhecido"}):
            self.assertEqual(shells.padrao(lista).id, "bash")

    def test_comando_na_subida_nos_tres_shells(self):
        bash = shells.com_comando(shells.Shell("bash", "Bash", "/b"), "claude")
        self.assertEqual(bash, ["/b", "-i", "-c", "trap : INT; claude; trap - INT; exec /b -i"])
        self.assertEqual(shells.com_comando(shells.Shell("fish", "Fish", "/f"), "claude"), ["/f", "-i", "-C", "claude"])
        self.assertIsNone(shells.com_comando(shells.Shell("nu", "Nushell", "/n"), "claude"))

    def test_comando_na_subida_roda_e_o_ctrl_c_volta_pro_shell(self):
        """Sem o trap, o Ctrl+C no programa encerrava a linha toda, e a aba fechava."""
        casa = Path(tempfile.mkdtemp(prefix="loom-casa-"))
        self.addCleanup(shutil.rmtree, casa, True)
        (casa / ".bashrc").write_text("PS1='prompt-pronto$ '\n")
        argv = shells.com_comando(shells.Shell("bash", "Bash", shutil.which("bash")), "echo rodou; sleep 30")
        proc = PtyProcess.spawn(argv, cwd=str(casa), env={**AMBIENTE, "HOME": str(casa)}, dimensions=(24, 80))
        self.addCleanup(proc.close)
        self.assertIn("rodou", ler_ate(proc, "rodou"))
        time.sleep(0.2)
        proc.write("\x03")
        self.assertIn("prompt-pronto", ler_ate(proc, "prompt-pronto"))
        self.assertTrue(proc.isalive(), "a aba fechou com o Ctrl+C")


@fora_do_windows
class TravaDeInstancia(ComPastaPropria):
    def outro_processo_tenta(self) -> bool:
        codigo = (
            "import sys; sys.path.insert(0, sys.argv[1]);"
            "from pathlib import Path; from loom import caminhos, instancia;"
            "caminhos.RAIZ = Path(sys.argv[2]);"
            "print(instancia.trava_em_arquivo('teste'))"
        )
        feito = subprocess.run(
            [sys.executable, "-c", codigo, str(Path(__file__).resolve().parent.parent), str(caminhos.RAIZ)],
            capture_output=True, text=True, timeout=30,
        )
        return feito.stdout.strip() == "True"

    def tearDown(self) -> None:
        for descritor in instancia._travas_em_arquivo.values():
            os.close(descritor)
        instancia._travas_em_arquivo.clear()
        super().tearDown()

    def test_outro_processo_nao_pega_e_o_mesmo_pega_de_novo(self):
        self.assertTrue(instancia.trava_em_arquivo("teste"))
        self.assertTrue(instancia.trava_em_arquivo("teste"))
        self.assertFalse(self.outro_processo_tenta())

    def test_quem_morre_solta_a_trava(self):
        self.assertTrue(self.outro_processo_tenta(), "o processo que saiu deixou a trava presa")
        self.assertTrue(instancia.trava_em_arquivo("teste"))

    def test_trava_nao_passa_pro_shell_filho(self):
        """Um shell aberto seguraria a trava com o Loom ja fechado, e ele nunca mais abriria."""
        self.assertTrue(instancia.trava_em_arquivo("teste"))
        descritor = instancia._travas_em_arquivo["teste"]
        self.assertFalse(os.get_inheritable(descritor))

    def test_nucleo_usa_a_mesma_trava_de_arquivo(self):
        self.assertTrue(nucleo.segurar_trava("teste"))
        self.assertFalse(self.outro_processo_tenta())


@fora_do_windows
class QuedaNoLinux(ComPastaPropria):
    def test_boot_id_identifica_a_partida(self):
        self.assertIsInstance(queda._id_do_boot(), str)
        self.assertTrue(queda.mesmo_boot(queda.boot_atual(), queda.boot_atual()))
        self.assertFalse(queda.mesmo_boot({"id": "outra", "em": 0.0}, queda.boot_atual()))

    def test_nascimento_distingue_pid_reaproveitado(self):
        self.assertIsNotNone(queda.criado_em(os.getpid()))
        self.assertGreater(queda.criado_em(os.getpid()), 0)
        proc = subprocess.Popen(["true"])
        proc.wait()
        self.assertIsNone(queda.criado_em(proc.pid))


@fora_do_windows
class AtalhoDaStatusLine(ComPastaPropria):
    def rodar(self, atalho: Path, *args, entrada: str = "{}") -> subprocess.CompletedProcess:
        return subprocess.run([str(atalho), *args], input=entrada, capture_output=True, text=True, timeout=10)

    def test_atalho_chama_o_coletor_e_passa_os_argumentos(self):
        projeto = self.raiz / "projeto com espaço"
        projeto.mkdir()
        (projeto / "coletar.py").write_text("import sys; print('coletor', *sys.argv[1:])")
        atalho = perfis.blindar_status_line(sys.executable, str(projeto), "echo barra-de-antes")
        self.assertEqual(atalho.name, "status-line.sh")
        self.assertTrue(atalho.stat().st_mode & stat.S_IXUSR, "sem o bit de execucao o Claude nao chama")
        self.assertEqual(self.rodar(atalho, "a b", "c").stdout.strip(), "coletor a b c")

    def test_sem_o_projeto_cai_na_barra_de_sempre_e_gancho_sai_calado(self):
        atalho = perfis.blindar_status_line(sys.executable, str(self.raiz / "sumiu"), "echo barra-de-antes")
        self.assertEqual(self.rodar(atalho).stdout.strip(), "barra-de-antes")
        for gancho in ("--hook", "--aviso"):
            feito = self.rodar(atalho, gancho, "p1")
            self.assertEqual((feito.stdout, feito.returncode), ("", 0), gancho)

    def test_sem_barra_de_antes_o_script_continua_valido(self):
        """O sh nao aceita bloco vazio, e um else sem nada quebrava a status line inteira."""
        atalho = perfis.blindar_status_line(sys.executable, str(self.raiz / "sumiu"), "")
        feito = self.rodar(atalho)
        self.assertEqual((feito.returncode, feito.stderr), (0, ""))
        self.assertEqual(perfis._anterior_do_atalho(atalho.read_text()), "")

    def test_anterior_volta_igual_do_atalho(self):
        atalho = perfis.blindar_status_line(sys.executable, str(self.raiz), 'minha-barra --tema "x"')
        self.assertEqual(perfis._anterior_do_atalho(atalho.read_text()), 'minha-barra --tema "x"')

    def test_ligar_e_desligar_a_principal_devolve_o_settings_como_era(self):
        casa = self.casa
        (casa / ".claude" / "settings.json").write_text(json.dumps({"statusLine": {"type": "command", "command": "minha-barra"}, "x": 1}))
        with unittest.mock.patch.object(perfis, "pasta_principal", return_value=casa / ".claude"):
            self.assertTrue(perfis.ligar_leitura_principal(sys.executable, str(self.raiz)))
            ligado = json.loads((casa / ".claude" / "settings.json").read_text())
            self.assertIn("status-line.sh", ligado["statusLine"]["command"])
            self.assertTrue(perfis.leitura_principal_ligada())
            self.assertTrue(perfis.desligar_leitura_principal())
        voltou = json.loads((casa / ".claude" / "settings.json").read_text())
        self.assertEqual(voltou["statusLine"]["command"], "minha-barra")
        self.assertEqual(voltou["x"], 1)
        self.assertNotIn("hooks", voltou)

    def test_gancho_do_atalho_e_reconhecido_como_nosso(self):
        self.assertTrue(perfis._gancho_nosso('"/d/status-line.sh" --hook __padrao__'))
        self.assertFalse(perfis._gancho_nosso('"/d/status-line.sh" __padrao__'))
        self.assertFalse(perfis._gancho_nosso('"/d/outra.sh" --hook __padrao__'))

    def test_reparar_aponta_o_atalho_antigo_pro_coletor_de_agora(self):
        velho = self.raiz / "velho" / "status-line.sh"
        velho.parent.mkdir()
        perfis._gravar_atalho(velho, perfis._texto_do_atalho_sh("/py/velho", "/projeto/velho", "barra"))
        (self.casa / ".claude" / "settings.json").write_text(json.dumps({"statusLine": {"command": f'"{velho}" __padrao__'}}))
        with unittest.mock.patch.object(perfis, "pasta_principal", return_value=self.casa / ".claude"):
            self.assertTrue(perfis.reparar_status_line_principal(sys.executable, str(self.raiz)))
        novo = velho.read_text()
        self.assertIn(str(self.raiz / "coletar.py"), novo)
        self.assertEqual(perfis._anterior_do_atalho(novo), "barra")

    def test_app_instalado_chama_o_executavel_ao_lado_da_janela(self):
        with unittest.mock.patch.object(sys, "frozen", True, create=True):
            self.assertEqual(perfis._chamada_coletor("/opt/Loom/Loom", "/x"), '"/opt/Loom/loom-coletor"')
            self.assertIn("exec /opt/Loom/loom-coletor", perfis._texto_do_atalho("/opt/Loom/Loom", "/x", ""))

    def test_auth_status_nao_abre_o_cli_inteiro(self):
        """O shell com lista jogaria fora o auth status e subiria o Claude sem terminal."""
        pasta = self.raiz / "conta"
        pasta.mkdir()
        (pasta / ".credentials.json").write_text("{}")
        perfil = unittest.mock.Mock(config_dir=pasta, ferramenta="claude")
        with unittest.mock.patch.object(perfis.subprocess, "run", return_value=unittest.mock.Mock(stdout="{}")) as rodar:
            perfis.perguntar_identidade(perfil)
        self.assertEqual(rodar.call_args.args[0][-2:], ["auth", "status"])
        self.assertFalse(rodar.call_args.kwargs["shell"])


@fora_do_windows
class EditorNoLinux(unittest.TestCase):
    def test_comando_escondido_no_nome_do_arquivo_nao_roda(self):
        """No sh, dentro de aspas duplas o $() e a crase ainda rodam: bastava clicar no caminho."""
        pasta = Path(tempfile.mkdtemp(prefix="loom-editor-"))
        self.addCleanup(shutil.rmtree, pasta, True)
        vitima = pasta / "vitima"
        arquivo = f"a$(touch {vitima})`touch {vitima}`.py"
        editor = pasta / "code"
        editor.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$0.args\"\n")
        editor.chmod(0o755)
        comando = mod_bridge.linha_do_editor(str(editor), arquivo, "7")
        subprocess.run(comando, shell=True, check=True, timeout=10)
        self.assertFalse(vitima.exists(), "o nome do arquivo virou comando")
        self.assertEqual((pasta / "code.args").read_text().splitlines(), ["-g", f"{arquivo}:7"])

    def test_nao_abre_no_programa_padrao_o_que_o_sistema_executaria(self):
        pasta = Path(tempfile.mkdtemp(prefix="loom-abrir-"))
        self.addCleanup(shutil.rmtree, pasta, True)
        comum, executavel = pasta / "nota.txt", pasta / "programa"
        comum.write_text("x")
        executavel.write_text("x")
        executavel.chmod(0o755)
        self.assertFalse(mod_bridge._roda_ao_abrir(str(comum)))
        self.assertTrue(mod_bridge._roda_ao_abrir(str(executavel)))
        for nome in ("atalho.desktop", "instala.sh", "app.AppImage"):
            self.assertTrue(mod_bridge._roda_ao_abrir(str(pasta / nome)), nome)


@fora_do_windows
class PathDoApp(unittest.TestCase):
    def test_pasta_do_usuario_entra_no_fim_e_so_se_existir(self):
        casa = Path(tempfile.mkdtemp(prefix="loom-path-"))
        self.addCleanup(shutil.rmtree, casa, True)
        (casa / ".local" / "bin").mkdir(parents=True)
        ambiente = {"PATH": "/usr/bin"}
        with unittest.mock.patch.dict(os.environ, {"HOME": str(casa)}):
            caminhos.completar_path(ambiente)
            self.assertEqual(ambiente["PATH"], f"/usr/bin:{casa}/.local/bin")
            caminhos.completar_path(ambiente)
            self.assertEqual(ambiente["PATH"], f"/usr/bin:{casa}/.local/bin", "repetiu a pasta")


class BarraDaJanela(unittest.TestCase):
    """O que a pagina pede pra janela sem a barra do sistema, com a janela falsa."""

    def pedir(self, acao: str, maximizada: bool = False):
        from types import SimpleNamespace

        from loom import app as mod_app

        janela = unittest.mock.Mock()
        falsa = SimpleNamespace(
            windowHandle=lambda: janela,
            isMaximized=lambda: maximizada,
            showMaximized=unittest.mock.Mock(),
            showNormal=unittest.mock.Mock(),
            showMinimized=unittest.mock.Mock(),
            close=unittest.mock.Mock(),
        )
        mod_app.MainWindow._pedido_da_janela(falsa, acao)
        return janela, falsa

    def test_mover_e_mudar_o_tamanho_ficam_com_o_compositor(self):
        from loom import app as mod_app

        janela, _ = self.pedir("mover")
        janela.startSystemMove.assert_called_once_with()
        janela, _ = self.pedir("borda:baixo-direita")
        janela.startSystemResize.assert_called_once_with(mod_app.BORDAS["baixo-direita"])

    def test_borda_que_nao_existe_nao_faz_nada(self):
        janela, _ = self.pedir("borda:inventada")
        janela.startSystemResize.assert_not_called()

    def test_maximizar_alterna(self):
        _, falsa = self.pedir("maximizar")
        falsa.showMaximized.assert_called_once_with()
        _, falsa = self.pedir("maximizar", maximizada=True)
        falsa.showNormal.assert_called_once_with()

    def test_minimizar(self):
        _, falsa = self.pedir("minimizar")
        falsa.showMinimized.assert_called_once_with()

    def test_pedido_desconhecido_nao_faz_nada(self):
        janela, falsa = self.pedir("sei-la")
        self.assertEqual((janela.mock_calls, falsa.close.mock_calls), ([], []))


class BarraMedidaNoMotorWeb(unittest.TestCase):
    """O desenho da barra de cima e da lista medido de verdade, num processo filho, sem shell nem janela."""

    def medir(self, largura: int, codigo: str) -> dict:
        from test_layout import medir_varios

        return medir_varios([codigo], largura=largura, altura=600)[0]

    def test_janela_estreita_mantem_os_botoes_dentro_e_a_barra_em_uma_linha(self):
        medida = self.medir(
            640,
            """(function () {
              document.body.classList.add("janela-propria");
              document.getElementById("janela-botoes").hidden = false;
              const fim = document.querySelector('[data-janela="fechar"]').getBoundingClientRect();
              const cromo = document.querySelector(".cromo").getBoundingClientRect();
              return JSON.stringify({ fim: fim.right, largura: innerWidth, altura: cromo.height });
            })()""",
        )
        self.assertLessEqual(medida["fim"], medida["largura"], "o fechar saiu da janela")
        self.assertEqual(medida["altura"], 44, "a barra quebrou de linha")

    def test_estado_comprido_do_cartao_cabe_inteiro(self):
        """O ready permission Edit dividia a linha com a conta e saia cortado no meio da palavra."""
        medida = self.medir(
            1200,
            """(function () {
              const lista = document.getElementById("abas");
              const c = document.createElement("div");
              c.className = "sessao aba aguardando";
              c.innerHTML = '<div class="sessao-topo"><span class="sessao-pasta">Curadoria</span></div>' +
                '<div class="sessao-origem">Claude Code · rito</div>' +
                '<div class="sessao-linha">ready <span class="ferramenta">permission Edit</span></div>' +
                '<div class="sessao-rodape"><span class="sessao-conta">Principal</span></div>';
              lista.append(c);
              const linha = c.querySelector(".sessao-linha");
              const origem = c.querySelector(".sessao-origem");
              return JSON.stringify({ visivel: getComputedStyle(c).display, sobra: linha.scrollWidth - linha.clientWidth,
                sobraOrigem: origem.scrollWidth - origem.clientWidth });
            })()""",
        )
        self.assertNotEqual(medida["visivel"], "none")
        self.assertLessEqual(medida["sobra"], 0, "o estado saiu cortado")
        self.assertLessEqual(medida["sobraOrigem"], 0, "a origem saiu cortada")

    def test_cartao_de_shell_puro_aparece_na_lista(self):
        medida = self.medir(
            1200,
            """(function () {
              const c = document.createElement("div");
              c.className = "sessao aba";
              c.innerHTML = '<div class="sessao-topo"><span class="sessao-pasta">bash</span></div><div class="sessao-origem"></div>' +
                '<div class="sessao-linha">shell</div><div class="sessao-rodape"><span class="sessao-conta">Principal</span></div>';
              document.getElementById("abas").append(c);
              const a = c.querySelector(".sessao-linha").getBoundingClientRect();
              const b = c.querySelector(".sessao-rodape").getBoundingClientRect();
              return JSON.stringify({ visivel: getComputedStyle(c).display, mesma: Math.abs(a.top - b.top) < a.height });
            })()""",
        )
        self.assertNotEqual(medida["visivel"], "none", "shell puro tem que estar na lista")
        self.assertTrue(medida["mesma"], "sem origem, o estado e a conta dividem a linha")


class VisualNovo(unittest.TestCase):
    def texto(self, nome: str) -> str:
        return (UI / nome).read_text(encoding="utf-8")

    def test_abas_ficam_na_lista_vertical_e_nao_no_topo(self):
        html = self.texto("index.html")
        cabecalho = html[html.index('<header class="cromo">'):html.index("</header>")]
        self.assertNotIn('id="novo"', cabecalho)
        self.assertNotIn("guias", html)
        lateral = html[html.index('<aside class="lateral"'):html.index("</aside>")]
        for elemento in ('id="novo"', 'id="seletor-layout"', 'id="abas"'):
            self.assertIn(elemento, lateral, elemento)
        self.assertNotIn(".guia", self.texto("app.js"))

    def test_nao_sobrou_cor_azulada_antiga_no_tema_do_terminal(self):
        app = self.texto("app.js")
        tema = app[app.index("const TEMA = {"):app.index("};", app.index("const TEMA = {"))]
        self.assertIn('background: "#111214"', tema)

    def test_barra_propria_so_fora_do_windows_e_com_saida(self):
        from loom import app as mod_app

        with unittest.mock.patch.object(mod_app.sys, "platform", "linux"):
            self.assertTrue(mod_app.usa_barra_propria({}))
            self.assertFalse(mod_app.usa_barra_propria({"barraDoSistema": True}))
        with unittest.mock.patch.object(mod_app.sys, "platform", "win32"):
            self.assertFalse(mod_app.usa_barra_propria({}))

    def test_ajuste_da_barra_do_sistema_e_gravado(self):
        self.assertIs(config.PADRAO["barraDoSistema"], False)
        self.assertIn('["barraDoSistema", "check"]', self.texto("app.js"))
        self.assertIn('id="cfg-barraDoSistema"', self.texto("index.html"))

    def test_toda_borda_da_pagina_existe_na_janela(self):
        import re

        from loom import app as mod_app

        bordas = set(re.findall(r'data-borda="([\w-]+)"', self.texto("index.html")))
        self.assertEqual(bordas, set(mod_app.BORDAS))


if __name__ == "__main__":
    unittest.main()
