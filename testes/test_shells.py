"""Achar o shell, subir com privilegio e ler o que ele escreve, sem subir shell nenhum.

O PATH, o registro do Windows, o sudo e o proprio pseudo console entram falsificados.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loom import pty_session, shells  # noqa: E402
from loom.pty_session import PtySession  # noqa: E402


def achar(mapa: dict):
    return unittest.mock.patch.object(shells.shutil, "which", side_effect=lambda nome: mapa.get(nome))


class DescobrirShells(unittest.TestCase):
    def test_git_bash_fora_do_path_e_achado_no_lugar_de_sempre(self):
        extra = shells.EXTRAS["bash"][0]
        with (
            achar({"cmd.exe": r"C:\Windows\System32\cmd.exe"}),
            unittest.mock.patch.object(shells.os.path, "exists", side_effect=lambda c: c == extra),
        ):
            achados = shells.descobrir()
        self.assertEqual([s.id for s in achados], ["cmd", "bash"])
        self.assertEqual((achados[1].caminho, achados[1].args), (extra, ("--login", "-i")))

    def test_sem_nada_instalado_a_lista_vem_vazia(self):
        with achar({}), unittest.mock.patch.object(shells.os.path, "exists", return_value=False):
            self.assertEqual(shells.descobrir(), [])

    def test_padrao_prefere_o_powershell_novo(self):
        def shell(sid: str) -> shells.Shell:
            return shells.Shell(sid, sid, sid + ".exe")

        self.assertEqual(shells.padrao([shell("cmd"), shell("powershell"), shell("pwsh")]).id, "pwsh")
        self.assertEqual(shells.padrao([shell("bash"), shell("cmd")]).id, "cmd")
        self.assertEqual(shells.padrao([shell("bash"), shell("wsl")]).id, "bash")

    def test_como_dict_leva_os_argumentos_em_lista(self):
        dados = shells.Shell("pwsh", "PowerShell 7", "pwsh.exe", ("-NoLogo",)).como_dict()
        self.assertEqual(dados["args"], ["-NoLogo"])

    def test_elevado_poe_o_sudo_na_frente_do_shell_inteiro(self):
        shell = shells.Shell("bash", "Git Bash", r"C:\Git\bin\bash.exe", ("--login", "-i"))
        self.assertEqual(
            shells.elevado(shell, r"C:\gsudo\gsudo.exe"), [r"C:\gsudo\gsudo.exe", r"C:\Git\bin\bash.exe", "--login", "-i"]
        )


class SudoDoWindows(unittest.TestCase):
    def elevacao(self, achados: dict, modo=None, saida: str = "uso: sudo", erro: Exception | None = None):
        provas = []

        def falso_run(argumentos, **opcoes):
            provas.append(argumentos)
            if erro is not None:
                raise erro
            return subprocess.CompletedProcess(argumentos, 0, stdout=saida, stderr="")

        with (
            unittest.mock.patch.dict(os.environ, {"SystemRoot": r"C:\Windows"}),
            achar(achados),
            unittest.mock.patch.object(shells.subprocess, "run", falso_run),
            unittest.mock.patch.object(shells, "_modo_do_sudo", return_value=modo),
        ):
            return shells.elevacao(), provas

    SUDO = {"sudo": r"C:\Windows\System32\sudo.exe"}

    def test_sem_sudo_nenhum_nao_ha_opcao(self):
        resposta, provas = self.elevacao({})
        self.assertEqual(resposta, {"disponivel": False, "motivo": "sem sudo", "comando": ""})
        self.assertEqual(provas, [])

    def test_cada_modo_do_registro_diz_o_motivo(self):
        for modo, motivo in (
            (shells.SUDO_DESLIGADO, "desligado"),
            (shells.SUDO_SEM_ENTRADA, "sem entrada"),
            (shells.SUDO_NOVA_JANELA, "nova janela"),
            (9, "desligado"),
        ):
            resposta, provas = self.elevacao(self.SUDO, modo)
            self.assertEqual((resposta["disponivel"], resposta["motivo"]), (False, motivo), modo)
            self.assertEqual(provas, [], "com o modo no registro nao se roda o sudo pra provar")

    def test_sudo_de_fora_do_windows_e_provado(self):
        resposta, provas = self.elevacao({"gsudo": r"C:\gsudo\gsudo.exe"})
        self.assertEqual(resposta, {"disponivel": True, "motivo": "", "comando": r"C:\gsudo\gsudo.exe"})
        self.assertEqual(provas, [[r"C:\gsudo\gsudo.exe", "--help"]])

    def test_prova_que_diz_desligado_tira_a_opcao(self):
        for texto in ("Sudo is disabled on this machine.", "O sudo esta desabilitado"):
            resposta, _ = self.elevacao(self.SUDO, None, saida=texto)
            self.assertEqual((resposta["disponivel"], resposta["motivo"]), (False, "desligado"), texto)

    def test_prova_que_falha_tira_a_opcao(self):
        for erro in (OSError("acesso negado"), subprocess.TimeoutExpired("sudo", 8)):
            resposta, _ = self.elevacao(self.SUDO, None, erro=erro)
            self.assertEqual(resposta, {"disponivel": False, "motivo": "sem sudo", "comando": ""})

    def test_so_o_da_pasta_do_sistema_conta_como_do_windows(self):
        with unittest.mock.patch.dict(os.environ, {"SystemRoot": r"C:\Windows"}):
            self.assertTrue(shells._do_windows(r"c:\windows\system32\sudo.exe"))
            self.assertFalse(shells._do_windows(r"C:\Windows\System32\sub\sudo.exe"))
            self.assertFalse(shells._do_windows(r"C:\gsudo\sudo.exe"))


@unittest.skipUnless(os.name == "nt", "registro e do Windows")
class ModoGravadoNoRegistro(unittest.TestCase):
    def ler(self, valor=None, erro: Exception | None = None):
        import winreg

        with (
            unittest.mock.patch.object(winreg, "OpenKey", side_effect=erro) as abrir,
            unittest.mock.patch.object(winreg, "QueryValueEx", return_value=(valor, winreg.REG_DWORD)),
        ):
            modo = shells._modo_do_sudo()
        return modo, abrir

    def test_numero_gravado_e_o_modo(self):
        modo, abrir = self.ler(3)
        self.assertEqual(modo, 3)
        self.assertEqual(abrir.call_args.args[1], shells.CHAVE_DO_SUDO)

    def test_chave_ausente_ou_valor_estranho_nao_diz_nada(self):
        self.assertIsNone(self.ler(erro=FileNotFoundError("sem chave"))[0])
        self.assertIsNone(self.ler("3")[0])


class ProcQueEscreve:
    """O PtyProcess do pywinpty, entregando pedacos e depois o fim."""

    def __init__(self, pedacos=(), saida: int | None = 0, erro: Exception | None = None, vivo: bool = True):
        self.pedacos = list(pedacos)
        self.exitstatus = saida
        self.erro = erro
        self.vivo = vivo
        self.pedidos: list[int] = []
        self.escrito: list[str] = []
        self.tamanhos: list[tuple[int, int]] = []
        self.pid = 4242

    def read(self, tamanho: int) -> str:
        self.pedidos.append(tamanho)
        if self.pedacos:
            return self.pedacos.pop(0)
        if self.erro is not None:
            raise self.erro
        return ""

    def isalive(self) -> bool:
        return self.vivo

    def write(self, texto: str) -> None:
        self.escrito.append(texto)

    def setwinsize(self, linhas: int, colunas: int) -> None:
        self.tamanhos.append((linhas, colunas))


class LeituraDoShell(unittest.TestCase):
    def sessao(self) -> tuple[PtySession, list, list]:
        sessao = PtySession("aba1", argv=["x"])
        saidas, fins = [], []
        sessao.output.connect(lambda sid, texto: saidas.append((sid, texto)))
        sessao.exited.connect(lambda sid, codigo: fins.append((sid, codigo)))
        return sessao, saidas, fins

    def test_pedacos_chegam_em_ordem_e_depois_o_codigo_de_saida(self):
        sessao, saidas, fins = self.sessao()
        proc = ProcQueEscreve(["PS> ", "dir\r\n"], saida=3)
        sessao._pump(proc)
        self.assertEqual(saidas, [("aba1", "PS> "), ("aba1", "dir\r\n")])
        self.assertEqual(fins, [("aba1", 3)])

    def test_le_em_blocos_grandes(self):
        """No padrao de 1 kB do winpty, um despejo grande virava centenas de sinais por segundo."""
        sessao, _, _ = self.sessao()
        proc = ProcQueEscreve(["a", "b"])
        sessao._pump(proc)
        self.assertEqual(set(proc.pedidos), {65536})

    def test_fim_do_canal_ou_erro_de_leitura_ainda_avisa_a_saida(self):
        for erro in (EOFError(), OSError("pipe fechado")):
            sessao, saidas, fins = self.sessao()
            sessao._pump(ProcQueEscreve(["x"], saida=None, erro=erro))
            self.assertEqual(saidas, [("aba1", "x")])
            self.assertEqual(fins, [("aba1", 0)], repr(erro))

    def test_aba_fechada_de_proposito_nao_avisa_nem_entrega(self):
        sessao, saidas, fins = self.sessao()
        sessao._stopping = True
        sessao._pump(ProcQueEscreve(["resto do shell"], saida=1))
        self.assertEqual((saidas, fins), ([], []))


class SessaoSemShell(unittest.TestCase):
    def test_redimensionar_manda_linhas_e_colunas_nessa_ordem(self):
        sessao = PtySession("aba1", argv=["x"])
        proc = ProcQueEscreve()
        sessao._proc = proc
        sessao.resize(132, 40)
        self.assertEqual(proc.tamanhos, [(40, 132)])
        self.assertEqual(sessao.tamanho, (132, 40))

    def test_tamanho_zero_vira_um(self):
        sessao = PtySession("aba1", argv=["x"])
        sessao.resize(0, -3)
        self.assertEqual(sessao.tamanho, (1, 1))

    def test_shell_morto_nao_recebe_tecla_nem_tamanho(self):
        sessao = PtySession("aba1", argv=["x"])
        proc = ProcQueEscreve(vivo=False)
        sessao._proc = proc
        sessao.write("dir\r")
        sessao.resize(80, 24)
        self.assertEqual((proc.escrito, proc.tamanhos), ([], []))
        self.assertFalse(sessao.alive)

    def test_pid_so_existe_com_processo(self):
        sessao = PtySession("aba1", argv=["x"])
        self.assertIsNone(sessao.pid)
        sessao._proc = ProcQueEscreve()
        self.assertEqual(sessao.pid, 4242)

    def test_ambiente_da_aba_chega_limpo_e_com_cor(self):
        with unittest.mock.patch.dict(os.environ, {"CLAUDE_CODE_ENTRYPOINT": "cli", "QT_QPA_PLATFORM": "x"}):
            sessao = PtySession("aba1", argv=["x"], env={"LOOM_ABA": "aba1", "CLAUDE_CONFIG_DIR": r"C:\conta"})
        self.assertEqual(sessao._env["LOOM_ABA"], "aba1")
        self.assertEqual(sessao._env["CLAUDE_CONFIG_DIR"], r"C:\conta")
        self.assertEqual(sessao._env["TERM"], "xterm-256color")
        self.assertNotIn("CLAUDE_CODE_ENTRYPOINT", sessao._env)
        self.assertNotIn("QT_QPA_PLATFORM", sessao._env)

    def test_shell_de_sempre_quando_nenhum_e_pedido(self):
        with unittest.mock.patch.object(pty_session.shutil, "which", side_effect=lambda n: {"powershell.exe": "ps.exe"}.get(n)):
            self.assertEqual(pty_session.default_shell(), ["ps.exe", "-NoLogo"])
        with (
            unittest.mock.patch.object(pty_session.shutil, "which", return_value=None),
            unittest.mock.patch.dict(os.environ, {"COMSPEC": r"C:\Windows\cmd.exe"}),
        ):
            self.assertEqual(pty_session.default_shell(), [r"C:\Windows\cmd.exe"])


class FilhosDaAba(unittest.TestCase):
    def test_sem_pid_ou_sem_foto_nao_da_pra_saber(self):
        self.assertIsNone(pty_session.tem_filhos(None))
        with unittest.mock.patch.object(pty_session, "_processos", return_value=None):
            self.assertIsNone(pty_session.tem_filhos(10))

    def test_so_o_console_do_windows_nao_conta(self):
        foto = [(10, 1, "pwsh.exe"), (11, 10, "OpenConsole.exe"), (12, 99, "claude.exe")]
        with unittest.mock.patch.object(pty_session, "_processos", return_value=foto):
            self.assertFalse(pty_session.tem_filhos(10))
        foto.append((13, 10, "node.exe"))
        with unittest.mock.patch.object(pty_session, "_processos", return_value=foto):
            self.assertTrue(pty_session.tem_filhos(10))


if __name__ == "__main__":
    unittest.main()
