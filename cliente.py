"""
cliente.py — Interface Gráfica (CustomTkinter) para o Jogo Multijogador (RPyC)

Arquitetura de threads:
  - Thread principal (Tkinter): renderiza a UI e processa eventos via after().
  - Thread RPyC (daemon): roda conn.serve(0.1) continuamente para receber
    callbacks do servidor.
  - Comunicação segura: callbacks colocam tuplas em uma queue.Queue;
    App._poll() consome essa fila no main thread a cada 120 ms.
"""

import os
import re
import queue
import threading
import tkinter.messagebox as tkmsg
import customtkinter as ctk
from PIL import Image
import rpyc

# ── Configuração visual global
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

HOST  = "127.0.0.1"
PORTA = 18861

_PASTA_OBJETOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "objetos")


# ── Utilitário: extrai (nome_obj, arte) de mensagens de reveal do servidor

def _parse_objeto(mensagem: str):
    if "objeto secreto" not in mensagem.lower():
        return None, None
    linhas = mensagem.split("\n")
    nome_obj, idx = None, None
    for i, linha in enumerate(linhas):
        if "objeto secreto" in linha.lower():
            m = re.search(r"\[([^\[\]\n]+)\]", linha)
            if m:
                nome_obj = m.group(1).strip()
                idx = i + 1
                break
    if nome_obj is None:
        return None, None
    _stops = ("guarde", "(dê", "dê apenas", "use [")
    arte_linhas = []
    for linha in linhas[idx:]:
        if any(linha.strip().lower().startswith(s) for s in _stops):
            break
        arte_linhas.append(linha)
    while arte_linhas and not arte_linhas[-1].strip():
        arte_linhas.pop()
    return nome_obj, "\n".join(arte_linhas)


# ── Serviço RPyC do cliente (callbacks do servidor → fila → Tkinter)

class ClienteService(rpyc.Service):
    """
    Recebe notificações push do servidor na thread RPyC.
    NUNCA toca em widgets diretamente — empurra eventos para _fila.
    """
    _fila: queue.Queue = None  # definido pelo App antes de conectar

    def exposed_notificar_sistema(self, mensagem: str):
        if ClienteService._fila:
            ClienteService._fila.put(("sistema", str(mensagem)))

    def exposed_receber_chat(self, remetente: str, texto: str):
        if ClienteService._fila:
            ClienteService._fila.put(("chat_pub", str(remetente), str(texto)))

    def exposed_receber_chat_privado(self, remetente: str, texto: str):
        if ClienteService._fila:
            ClienteService._fila.put(("chat_priv", str(remetente), str(texto)))

    def exposed_receber_dica(self, remetente: str, dica: str):
        if ClienteService._fila:
            ClienteService._fila.put(("dica", str(remetente), str(dica)))

    def exposed_notificar_troca_solicitada(self, solicitante: str):
        if ClienteService._fila:
            ClienteService._fila.put(("troca_solicitada", str(solicitante)))

    def exposed_notificar_espionagem(self, espiao: str, pego: bool):
        if ClienteService._fila:
            ClienteService._fila.put(("espionagem", str(espiao), bool(pego)))


# ── Diálogos modais reutilizáveis

class _DialogoBase(ctk.CTkToplevel):
    def __init__(self, parent, titulo: str, w: int = 380, h: int = 220):
        super().__init__(parent)
        self.title(titulo)
        self.geometry(f"{w}x{h}")
        self.resizable(False, False)
        self.grab_set()
        self.lift()
        self.focus_force()


class DialogoTexto(_DialogoBase):
    """Coleta uma string simples (ex: palavra de dica, palpite)."""

    def __init__(self, parent, titulo: str, prompt: str,
                 placeholder: str = ""):
        super().__init__(parent, titulo)
        self.valor: str | None = None

        ctk.CTkLabel(self, text=prompt, wraplength=330,
                     font=ctk.CTkFont(size=13)).pack(padx=24, pady=(24, 6))
        self._e = ctk.CTkEntry(self, width=330,
                               placeholder_text=placeholder)
        self._e.pack(padx=24, pady=4)
        self._e.focus()
        self._e.bind("<Return>", lambda _: self._ok())

        fb = ctk.CTkFrame(self, fg_color="transparent")
        fb.pack(pady=(14, 0))
        ctk.CTkButton(fb, text="Confirmar", width=130,
                      command=self._ok).pack(side="left", padx=6)
        ctk.CTkButton(fb, text="Cancelar", width=130,
                      fg_color="gray30", hover_color="gray40",
                      command=self.destroy).pack(side="left", padx=6)

    def _ok(self):
        self.valor = self._e.get().strip()
        self.destroy()


class DialogoSelecao(_DialogoBase):
    """Seleciona um jogador via ComboBox + opcionalmente coleta texto."""

    def __init__(self, parent, titulo: str, opcoes: list,
                 label_sel: str, label_txt: str = "",
                 placeholder_txt: str = ""):
        h = 310 if label_txt else 220
        super().__init__(parent, titulo, h=h)
        self.jogador: str | None = None
        self.texto:   str | None = None

        ctk.CTkLabel(self, text=label_sel,
                     font=ctk.CTkFont(size=13)).pack(padx=24, pady=(20, 4))
        self._combo = ctk.CTkComboBox(self, values=opcoes, width=330)
        self._combo.pack(padx=24, pady=4)
        if opcoes:
            self._combo.set(opcoes[0])

        self._e = None
        if label_txt:
            ctk.CTkLabel(self, text=label_txt,
                         font=ctk.CTkFont(size=13)).pack(padx=24, pady=(14, 4))
            self._e = ctk.CTkEntry(self, width=330,
                                   placeholder_text=placeholder_txt)
            self._e.pack(padx=24, pady=4)

        fb = ctk.CTkFrame(self, fg_color="transparent")
        fb.pack(pady=(14, 0))
        ctk.CTkButton(fb, text="Confirmar", width=130,
                      command=self._ok).pack(side="left", padx=6)
        ctk.CTkButton(fb, text="Cancelar", width=130,
                      fg_color="gray30", hover_color="gray40",
                      command=self.destroy).pack(side="left", padx=6)

    def _ok(self):
        self.jogador = self._combo.get().strip()
        if self._e:
            self.texto = self._e.get().strip()
        self.destroy()


# ── Tela de Login

class TelaLogin(ctk.CTkFrame):
    def __init__(self, master, ao_conectar):
        super().__init__(master, fg_color="transparent")
        self._ao_conectar = ao_conectar
        self._construir()

    def _construir(self):
        card = ctk.CTkFrame(self, corner_radius=16, width=440, height=340)
        card.place(relx=0.5, rely=0.5, anchor="center")
        card.pack_propagate(False)

        ctk.CTkLabel(card, text="🎮  Jogo de Adivinhação",
                     font=ctk.CTkFont(size=24, weight="bold")).pack(pady=(36, 4))
        ctk.CTkLabel(card, text="Multiplayer via RPyC",
                     text_color="gray55",
                     font=ctk.CTkFont(size=13)).pack(pady=(0, 26))

        ctk.CTkLabel(card, text="Seu nome no jogo:",
                     anchor="w").pack(padx=40, anchor="w")
        self._entry = ctk.CTkEntry(card, width=360, height=42,
                                   placeholder_text="Digite seu nome…")
        self._entry.pack(padx=40, pady=(4, 18))
        self._entry.bind("<Return>", lambda _: self._entrar())
        self._entry.focus()

        self._btn = ctk.CTkButton(card, text="Entrar no Lobby",
                                  height=44, command=self._entrar)
        self._btn.pack(padx=40, fill="x")

        self._status = ctk.CTkLabel(card, text="",
                                    font=ctk.CTkFont(size=11),
                                    text_color="gray50")
        self._status.pack(pady=(10, 0))

    def _entrar(self):
        nome = self._entry.get().strip()
        if not nome:
            self._status.configure(
                text="⚠  Digite um nome para continuar.",
                text_color="#e57373")
            return
        self._btn.configure(state="disabled", text="Conectando…")
        self._status.configure(text="")
        self._ao_conectar(nome)

    def mostrar_erro(self, msg: str):
        self._btn.configure(state="normal", text="Entrar no Lobby")
        self._status.configure(text=f"⚠  {msg}", text_color="#e57373")


# ── Tela de Lobby

class TelaLobby(ctk.CTkFrame):
    def __init__(self, master, nome: str, anfitriao: str,
                 jogadores: list, ao_iniciar):
        super().__init__(master, fg_color="transparent")
        self._nome      = nome
        self._anfitriao = anfitriao
        self._construir(jogadores, ao_iniciar)

    def _construir(self, jogadores: list, ao_iniciar):
        card = ctk.CTkFrame(self, corner_radius=16, width=500)
        card.place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(card, text="⏳  Sala de Espera",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(28, 4))

        eh_anf    = self._nome == self._anfitriao
        papel_txt = "Você é o ANFITRIÃO" if eh_anf else f"Aguardando {self._anfitriao} iniciar"
        papel_cor = "#4fc3f7" if eh_anf else "gray55"
        ctk.CTkLabel(card, text=papel_txt, text_color=papel_cor,
                     font=ctk.CTkFont(size=12)).pack(pady=(0, 16))

        ctk.CTkLabel(card, text="Jogadores conectados:",
                     font=ctk.CTkFont(weight="bold"),
                     anchor="w").pack(padx=28, anchor="w")

        self._lista = ctk.CTkTextbox(
            card, width=444, height=170, state="disabled",
            font=ctk.CTkFont(family="Courier New", size=13))
        self._lista.pack(padx=28, pady=8)
        self._set_lista(jogadores)

        ctk.CTkButton(
            card, text="🚀  Iniciar Partida", height=44,
            state="normal" if eh_anf else "disabled",
            command=ao_iniciar
        ).pack(padx=28, pady=(8, 28), fill="x")

    def _set_lista(self, jogadores: list):
        self._lista.configure(state="normal")
        self._lista.delete("1.0", "end")
        for j in jogadores:
            icone = "👑 " if j == self._anfitriao else "   "
            self._lista.insert("end", f"{icone}{j}\n")
        self._lista.configure(state="disabled")

    def adicionar_jogador(self, nome: str):
        self._lista.configure(state="normal")
        self._lista.insert("end", f"   {nome}\n")
        self._lista.configure(state="disabled")


# ── Tela do Jogo (Dashboard principal)

class TelaJogo(ctk.CTkFrame):
    def __init__(self, master, nome: str, srv):
        super().__init__(master, fg_color="transparent")
        self._nome = nome
        self._srv  = srv
        self._construir()

    # ─────────────────────────────────── Layout

    def _construir(self):
        # Header
        hdr = ctk.CTkFrame(self, height=50, corner_radius=0,
                           fg_color=("gray88", "gray18"))
        hdr.pack(fill="x", side="top")
        hdr.pack_propagate(False)

        ctk.CTkLabel(hdr, text="🎮  Adivinhação RPyC",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(
            side="left", padx=16)
        self._lbl_turno = ctk.CTkLabel(
            hdr, text="Aguardando início…",
            font=ctk.CTkFont(size=12), text_color="#4fc3f7")
        self._lbl_turno.pack(side="left", padx=16)
        self._lbl_pts = ctk.CTkLabel(
            hdr, text="", font=ctk.CTkFont(size=12),
            text_color="#a5d6a7")
        self._lbl_pts.pack(side="right", padx=16)

        # Corpo em 3 colunas
        corpo = ctk.CTkFrame(self, fg_color="transparent")
        corpo.pack(fill="both", expand=True, padx=8, pady=8)
        corpo.grid_columnconfigure(0, weight=2)
        corpo.grid_columnconfigure(1, weight=3)
        corpo.grid_columnconfigure(2, weight=3)
        corpo.grid_rowconfigure(0, weight=1)

        self._col_acoes(corpo).grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        self._col_objeto(corpo).grid(row=0, column=1, sticky="nsew", padx=4)
        self._col_logs(corpo).grid( row=0, column=2, sticky="nsew", padx=(4, 0))

    # ── Coluna 1: Botões de ação

    def _col_acoes(self, parent) -> ctk.CTkFrame:
        f = ctk.CTkFrame(parent, corner_radius=12)
        ctk.CTkLabel(f, text="Ações",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(
            pady=(14, 2))

        def _sep(txt):
            ctk.CTkLabel(f, text=txt, text_color="gray45",
                         font=ctk.CTkFont(size=10)).pack(
                anchor="w", padx=12, pady=(8, 1))

        def _btn(label, cmd, cor=None):
            kw = dict(text=label, command=cmd, height=34, anchor="w")
            if cor:
                kw.update(fg_color=cor, hover_color=cor)
            ctk.CTkButton(f, **kw).pack(fill="x", padx=12, pady=2)

        _sep("── DICAS ───────────────────────")
        _btn("📢  Enviar Dica Pública",      self._enviar_dica)
        _btn("📋  Ver Histórico de Dicas",   self._historico_dicas, "#1e2d3d")

        _sep("── TROCAS ──────────────────────")
        _btn("🔄  Solicitar Troca Privada",  self._solicitar_troca)
        _btn("↩️   Aceitar / Recusar Troca",  self._aceitar_troca)
        _btn("👁   Espionar Troca",           self._espionar,        "#3d1e1e")

        _sep("── JOGO ────────────────────────")
        _btn("🎯  Fazer Palpite",             self._palpite,         "#1e3d1e")
        _btn("⏭   Passar a Vez",             self._passar_vez,      "#2d2d10")

        _sep("── INFO ─────────────────────────")
        _btn("🏆  Ver Placar",                self._placar)
        _btn("👥  Ver Jogadores",             self._ver_jogadores)
        _btn("🚨  Denunciar Espião",          self._denunciar,       "#3d1e1e")

        return f

    # ── Coluna 2: Objeto secreto

    def _col_objeto(self, parent) -> ctk.CTkFrame:
        f = ctk.CTkFrame(parent, corner_radius=12)

        ctk.CTkLabel(f, text="Meu Objeto Secreto",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(
            pady=(14, 2))
        self._lbl_nome_obj = ctk.CTkLabel(
            f, text="Aguardando início da partida…",
            text_color="gray50", font=ctk.CTkFont(size=13))
        self._lbl_nome_obj.pack(pady=(0, 8))

        arte_frame = ctk.CTkFrame(
            f, corner_radius=8,
            fg_color=("gray92", "gray13"),
            border_color="#4fc3f7", border_width=1)
        arte_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        ctk.CTkLabel(arte_frame, text="✦  CONFIDENCIAL  ✦",
                     text_color="#4fc3f7",
                     font=ctk.CTkFont(size=9)).pack(
            anchor="ne", padx=8, pady=(4, 0))

        self._img_label = ctk.CTkLabel(arte_frame, text="")
        self._img_label.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self._ctk_img  = None
        self._obj_pil  = None

        arte_frame.bind("<Configure>", self._redimensionar_imagem)

        return f

    # ── Coluna 3: Notificações + Chat

    def _col_logs(self, parent) -> ctk.CTkFrame:
        f = ctk.CTkFrame(parent, corner_radius=12)
        f.grid_rowconfigure(0, weight=3)
        f.grid_rowconfigure(1, weight=2)
        f.grid_columnconfigure(0, weight=1)

        # Notificações
        notif = ctk.CTkFrame(f, fg_color="transparent")
        notif.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 4))
        notif.grid_rowconfigure(1, weight=1)
        notif.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(notif, text="Notificações",
                     font=ctk.CTkFont(size=13, weight="bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 4))
        self._txt_log = ctk.CTkTextbox(
            notif, state="disabled", font=ctk.CTkFont(size=11))
        self._txt_log.grid(row=1, column=0, sticky="nsew")

        # Chat
        chat = ctk.CTkFrame(f, fg_color="transparent")
        chat.grid(row=1, column=0, sticky="nsew", padx=10, pady=(4, 10))
        chat.grid_rowconfigure(1, weight=1)
        chat.grid_columnconfigure(0, weight=1)

        hdr_c = ctk.CTkFrame(chat, fg_color="transparent")
        hdr_c.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(hdr_c, text="Chat",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(
            side="left")
        ctk.CTkButton(hdr_c, text="Privado", width=72, height=24,
                      fg_color="#1a3a5c", hover_color="#2a4a6c",
                      command=self._chat_privado).pack(side="right")

        self._txt_chat = ctk.CTkTextbox(
            chat, state="disabled",
            font=ctk.CTkFont(size=11), height=100)
        self._txt_chat.grid(row=1, column=0, sticky="nsew", pady=(4, 4))

        inp = ctk.CTkFrame(chat, fg_color="transparent")
        inp.grid(row=2, column=0, sticky="ew")
        inp.grid_columnconfigure(0, weight=1)

        self._entry_chat = ctk.CTkEntry(
            inp, placeholder_text="Mensagem pública…")
        self._entry_chat.grid(row=0, column=0, sticky="ew")
        self._entry_chat.bind("<Return>", lambda _: self._enviar_chat())
        ctk.CTkButton(inp, text="Enviar", width=70,
                      command=self._enviar_chat).grid(
            row=0, column=1, padx=(4, 0))

        return f

    # ─────────────────────────────────── Helpers de UI

    def _outros(self) -> list:
        try:
            return [j for j in list(self._srv.jogadores()) if j != self._nome]
        except Exception:
            return []

    def _log(self, msg: str, icon: str = "⚙"):
        self._txt_log.configure(state="normal")
        self._txt_log.insert("end", f"{icon}  {msg}\n")
        self._txt_log.see("end")
        self._txt_log.configure(state="disabled")

    def _chat_ins(self, remetente: str, texto: str, privado: bool = False):
        tag  = "[priv]" if privado else "[pub] "
        nome = "★ você" if remetente == self._nome else remetente
        self._txt_chat.configure(state="normal")
        self._txt_chat.insert("end", f"{tag} {nome}: {texto}\n")
        self._txt_chat.see("end")
        self._txt_chat.configure(state="disabled")

    def _redimensionar_imagem(self, event=None):
        if self._obj_pil is None:
            return
        if event:
            w = max(event.width - 20, 50)
            h = max(event.height - 44, 50)
        else:
            self._img_label.update_idletasks()
            w = max(self._img_label.winfo_width(), 50)
            h = max(self._img_label.winfo_height(), 50)
        img = self._obj_pil.copy()
        img.thumbnail((w, h), Image.LANCZOS)
        ctk_img = ctk.CTkImage(light_image=img, dark_image=img,
                               size=(img.width, img.height))
        self._ctk_img = ctk_img
        self._img_label.configure(image=ctk_img, text="")

    def _set_objeto(self, nome_obj: str, arte: str = ""):
        self._lbl_nome_obj.configure(
            text=f"[ {nome_obj.upper()} ]",
            text_color="#4fc3f7",
            font=ctk.CTkFont(size=15, weight="bold"))
        caminho = os.path.join(_PASTA_OBJETOS, f"{nome_obj.lower()}.png")
        try:
            self._obj_pil = Image.open(caminho).convert("RGBA")
            self._redimensionar_imagem()
        except Exception:
            self._obj_pil = None
            self._img_label.configure(image=None, text=arte or f"[ {nome_obj} ]",
                                      font=ctk.CTkFont(family="Courier New", size=11))

    def set_turno(self, turno: str, rodada: int, num_t: int):
        eh_sua   = turno == self._nome
        marcador = "  ◀ SUA VEZ" if eh_sua else ""
        self._lbl_turno.configure(
            text=f"Rodada {rodada} | Turno {num_t} | Vez: {turno}{marcador}",
            text_color="#ffeb3b" if eh_sua else "#4fc3f7")

    def set_pts(self, pts: int):
        self._lbl_pts.configure(text=f"Seus pontos: {pts} pts")

    def _atualizar_header(self):
        try:
            t   = str(self._srv.turno_atual())
            r   = int(self._srv.rodada_atual())
            n   = int(self._srv.num_turno())
            pts = dict(self._srv.placar()).get(self._nome, 0)
            self.set_turno(t, r, n)
            self.set_pts(pts)
        except Exception:
            pass

    # ─────────────────────────────────── Ações dos botões

    def _enviar_dica(self):
        d = DialogoTexto(self, "Enviar Dica",
                         "Digite sua dica (UMA palavra):", "ex: brilhante")
        self.wait_window(d)
        if not d.valor:
            return
        if len(d.valor.split()) > 1:
            tkmsg.showwarning("Atenção",
                              "A dica deve ser uma única palavra.",
                              parent=self)
            return
        try:
            self._log(str(self._srv.enviar_dica(d.valor)))
        except Exception as e:
            self._log(f"Erro: {e}", "!")

    def _historico_dicas(self):
        try:
            dicas = list(self._srv.historico_dicas())
        except Exception as e:
            tkmsg.showerror("Erro", str(e), parent=self)
            return
        win = ctk.CTkToplevel(self)
        win.title("Histórico de Dicas")
        win.grab_set()
        txt = ctk.CTkTextbox(
            win, width=440, height=300,
            font=ctk.CTkFont(family="Courier New", size=12))
        txt.pack(padx=16, pady=16)
        txt.insert("end", f"{'Turno':>5}  {'Jogador':<16}  Dica\n")
        txt.insert("end", "─" * 40 + "\n")
        if not dicas:
            txt.insert("end", "(nenhuma dica ainda)")
        else:
            for remetente, dica, turno in dicas:
                txt.insert("end",
                           f"{turno:>5}  {remetente:<16}  '{dica}'\n")
        txt.configure(state="disabled")

    def _solicitar_troca(self):
        outros = self._outros()
        if not outros:
            tkmsg.showinfo("Aviso", "Nenhum outro jogador.", parent=self)
            return
        d = DialogoSelecao(self, "Solicitar Troca", outros,
                           "Trocar dica com quem?",
                           "Sua palavra (pode ser mentira):", "ex: frio")
        self.wait_window(d)
        if not d.jogador or not d.texto:
            return
        try:
            self._log(str(self._srv.solicitar_troca(d.jogador, d.texto)))
        except Exception as e:
            self._log(f"Erro: {e}", "!")

    def _aceitar_troca(self):
        outros = self._outros()
        if not outros:
            tkmsg.showinfo("Aviso", "Nenhum outro jogador.", parent=self)
            return
        d = DialogoSelecao(self, "Responder Troca", outros,
                           "Quem fez a solicitação?")
        self.wait_window(d)
        if not d.jogador:
            return
        aceitar = tkmsg.askyesno(
            "Aceitar Troca?",
            f"Aceitar a troca de '{d.jogador}'?", parent=self)
        if aceitar:
            d2 = DialogoTexto(self, "Sua Palavra",
                              "Sua palavra para a troca:",
                              "ex: quente")
            self.wait_window(d2)
            try:
                self._log(str(
                    self._srv.aceitar_troca(d.jogador, d2.valor or "")))
            except Exception as e:
                self._log(f"Erro: {e}", "!")
        else:
            try:
                self._log(str(self._srv.recusar_troca(d.jogador)))
            except Exception as e:
                self._log(f"Erro: {e}", "!")

    def _espionar(self):
        outros = self._outros()
        if len(outros) < 2:
            tkmsg.showinfo(
                "Aviso", "São necessários 2+ outros jogadores.",
                parent=self)
            return
        d1 = DialogoSelecao(self, "Espionar — Jogador A",
                            outros, "Jogador A da troca:")
        self.wait_window(d1)
        if not d1.jogador:
            return
        restantes = [j for j in outros if j != d1.jogador]
        d2 = DialogoSelecao(self, "Espionar — Jogador B",
                            restantes, "Jogador B da troca:")
        self.wait_window(d2)
        if not d2.jogador:
            return
        try:
            self._log(str(self._srv.espionar(d1.jogador, d2.jogador)), "👁")
        except Exception as e:
            self._log(f"Erro: {e}", "!")

    def _palpite(self):
        outros = self._outros()
        if not outros:
            tkmsg.showinfo("Aviso", "Nenhum alvo disponível.", parent=self)
            return
        d = DialogoSelecao(self, "Fazer Palpite", outros,
                           "Adivinhar objeto de quem?",
                           "Seu palpite:", "ex: espada")
        self.wait_window(d)
        if not d.jogador or not d.texto:
            return
        try:
            self._log(str(self._srv.palpite(d.jogador, d.texto)), "🎯")
        except Exception as e:
            self._log(f"Erro: {e}", "!")

    def _passar_vez(self):
        try:
            self._log(str(self._srv.passar_vez()))
        except Exception as e:
            self._log(f"Erro: {e}", "!")

    def _placar(self):
        try:
            placar = dict(self._srv.placar())
        except Exception as e:
            tkmsg.showerror("Erro", str(e), parent=self)
            return
        win = ctk.CTkToplevel(self)
        win.title("Placar Atual")
        win.grab_set()
        medalhas = {1: "🥇", 2: "🥈", 3: "🥉"}
        txt = ctk.CTkTextbox(win, width=320, height=240,
                             font=ctk.CTkFont(size=13))
        txt.pack(padx=16, pady=16)
        txt.insert("end", "  PLACAR ATUAL\n")
        txt.insert("end", "  " + "─" * 26 + "\n")
        for i, (j, pts) in enumerate(
                sorted(placar.items(), key=lambda x: -x[1]), 1):
            icon  = medalhas.get(i, f"  {i}.")
            marca = "  ← você" if j == self._nome else ""
            txt.insert("end", f"  {icon}  {j}: {pts} pts{marca}\n")
        txt.configure(state="disabled")

    def _ver_jogadores(self):
        try:
            jogadores = list(self._srv.jogadores())
        except Exception as e:
            tkmsg.showerror("Erro", str(e), parent=self)
            return
        win = ctk.CTkToplevel(self)
        win.title("Jogadores Conectados")
        win.resizable(False, False)
        win.grab_set()
        txt = ctk.CTkTextbox(win, width=300, height=200,
                             font=ctk.CTkFont(size=13))
        txt.pack(padx=16, pady=16)
        txt.insert("end", "  JOGADORES CONECTADOS\n")
        txt.insert("end", "  " + "─" * 24 + "\n")
        for j in jogadores:
            marca = "  ← você" if j == self._nome else ""
            txt.insert("end", f"   {j}{marca}\n")
        txt.configure(state="disabled")

    def _denunciar(self):
        try:
            ha = bool(self._srv.espionagens_pendentes())
        except Exception:
            ha = False
        if not ha:
            tkmsg.showinfo(
                "Espionagem",
                "Nenhuma espionagem suspeita nesta rodada.",
                parent=self)
            return
        outros = self._outros()
        d = DialogoSelecao(self, "Denunciar Espião",
                           outros, "Quem você deseja denunciar?")
        self.wait_window(d)
        if not d.jogador:
            return
        try:
            self._log(
                str(self._srv.denunciar_espionagem(d.jogador)), "🚨")
        except Exception as e:
            self._log(f"Erro: {e}", "!")

    def _enviar_chat(self):
        msg = self._entry_chat.get().strip()
        if not msg:
            return
        self._entry_chat.delete(0, "end")
        try:
            self._srv.chat(msg)
        except Exception as e:
            self._log(f"Erro no chat: {e}", "!")

    def _chat_privado(self):
        outros = self._outros()
        if not outros:
            tkmsg.showinfo("Aviso", "Nenhum outro jogador.", parent=self)
            return
        d = DialogoSelecao(self, "Chat Privado", outros,
                           "Enviar para quem?",
                           "Mensagem:", "mensagem privada…")
        self.wait_window(d)
        if not d.jogador or not d.texto:
            return
        try:
            self._srv.chat_privado(d.jogador, d.texto)
            self._chat_ins(
                self._nome, f"[para {d.jogador}] {d.texto}",
                privado=True)
        except Exception as e:
            self._log(f"Erro: {e}", "!")

    # ─────────────────────────────────── Despacho de eventos da fila

    def processar_evento(self, ev: tuple):
        tipo = ev[0]

        if tipo == "sistema":
            msg = ev[1]
            nome_obj, arte = _parse_objeto(msg)
            if nome_obj:
                self._set_objeto(nome_obj, arte)
                self._log(msg.split("\n")[0])
            else:
                self._log(msg)
            self._atualizar_header()

        elif tipo == "dica":
            _, rem, dica = ev
            self._log(f"Dica de {rem}: '{dica}'", "💬")

        elif tipo == "chat_pub":
            _, rem, txt = ev
            self._chat_ins(rem, txt)

        elif tipo == "chat_priv":
            _, rem, txt = ev
            self._chat_ins(rem, txt, privado=True)

        elif tipo == "troca_solicitada":
            self._log(
                f"{ev[1]} quer trocar dicas! "
                f"Use 'Aceitar / Recusar Troca'.", "🔄")

        elif tipo == "espionagem":
            esp, pego = ev[1], ev[2]
            if pego:
                self._log(
                    f"{esp} foi detectado espiando! "
                    f"Use 'Denunciar Espião'.", "👀")
            else:
                self._log("Alerta: alguém pode estar espiando.", "⚠")


# ── Aplicação principal

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Jogo de Adivinhação — RPyC")
        self.geometry("1120x700")
        self.minsize(920, 580)

        self._fila           = queue.Queue()
        self._conn           = None
        self._srv            = None
        self._nome           = None
        self._tela           = None   # tela ativa no momento
        self._votacao_aberta = False

        ClienteService._fila = self._fila

        self._ir_para_login()
        self.after(120, self._poll)
        self.protocol("WM_DELETE_WINDOW", self._fechar)

    # ── Navegação entre telas

    def _trocar_tela(self, nova: ctk.CTkFrame):
        if self._tela:
            self._tela.destroy()
        self._tela = nova
        nova.pack(fill="both", expand=True)

    def _ir_para_login(self):
        self._trocar_tela(TelaLogin(self, self._conectar))

    def _ir_para_lobby(self):
        jogadores = list(self._srv.jogadores_lobby())
        anfitriao = str(self._srv.anfitriao())
        self._trocar_tela(
            TelaLobby(self, self._nome, anfitriao,
                      jogadores, self._iniciar_jogo))

    def _ir_para_jogo(self):
        self._trocar_tela(TelaJogo(self, self._nome, self._srv))

    # ── Conexão RPyC

    def _conectar(self, nome: str):
        try:
            conn = rpyc.connect(
                HOST, PORTA,
                service=ClienteService,
                config={"allow_public_attrs": True,
                        "allow_pickle":       True,
                        "sync_request_timeout": 300},
            )
        except ConnectionRefusedError:
            self._tela.mostrar_erro(
                "Servidor não encontrado. Execute servidor.py primeiro.")
            return
        except Exception as e:
            self._tela.mostrar_erro(f"Erro de conexão: {e}")
            return

        resp = str(conn.root.entrar(nome))
        if resp.startswith("ERRO"):
            self._tela.mostrar_erro(resp)
            conn.close()
            return

        self._conn = conn
        self._srv  = conn.root
        self._nome = nome

        # Thread de callbacks: processa mensagens recebidas do servidor
        threading.Thread(target=self._serve_loop, daemon=True).start()
        self._ir_para_lobby()

    def _serve_loop(self):
        """Roda na thread RPyC — nunca toca na UI diretamente."""
        while True:
            try:
                self._conn.serve(0.1)
            except EOFError:
                break
            except Exception:
                pass

    def _iniciar_jogo(self):
        try:
            resp = str(self._srv.iniciar_jogo())
            if resp.startswith("ERRO"):
                tkmsg.showerror("Erro", resp, parent=self)
        except Exception as e:
            tkmsg.showerror("Erro", str(e), parent=self)

    # ── Polling da fila (thread-safe — roda sempre no main thread)

    def _poll(self):
        try:
            while True:
                self._despachar(self._fila.get_nowait())
        except queue.Empty:
            pass
        finally:
            self.after(120, self._poll)

    def _despachar(self, ev: tuple):
        tipo = ev[0]

        if tipo == "sistema":
            # Transição automática lobby → jogo
            try:
                em_jogo = bool(self._srv.jogo_iniciado())
            except Exception:
                em_jogo = False

            if em_jogo and not isinstance(self._tela, TelaJogo):
                self._ir_para_jogo()

            if isinstance(self._tela, TelaJogo):
                self._tela.processar_evento(ev)
            elif isinstance(self._tela, TelaLobby):
                msg = ev[1]
                if "entrou no lobby" in msg.lower():
                    self._tela.adicionar_jogador(msg.split()[0])

            # Abre janela de votação se necessário
            try:
                if (not self._votacao_aberta
                        and bool(self._srv.votacao_ativa())):
                    self._abrir_votacao()
            except Exception:
                pass

        elif isinstance(self._tela, TelaJogo):
            self._tela.processar_evento(ev)

    def _abrir_votacao(self):
        self._votacao_aberta = True

        try:
            placar = dict(self._srv.placar())
        except Exception:
            placar = {}

        win = ctk.CTkToplevel(self)
        win.title("Votação — Fim da Rodada")
        win.resizable(False, False)
        win.grab_set()
        win.lift()

        ctk.CTkLabel(
            win, text="FIM DA RODADA",
            font=ctk.CTkFont(size=17, weight="bold")).pack(pady=(24, 4))
        ctk.CTkLabel(
            win,
            text="Todos devem votar para continuar ou encerrar.",
            text_color="gray55").pack(pady=(0, 12))

        if placar:
            txt = ctk.CTkTextbox(win, width=300, height=130,
                                 font=ctk.CTkFont(size=12))
            txt.pack(padx=20, pady=(0, 12))
            txt.insert("end", "Placar:\n" + "─" * 20 + "\n")
            for j, pts in sorted(placar.items(), key=lambda x: -x[1]):
                txt.insert("end", f"  {j}: {pts} pts\n")
            txt.configure(state="disabled")

        fb = ctk.CTkFrame(win, fg_color="transparent")
        fb.pack(pady=(0, 24))

        def _votar(opcao: str):
            try:
                self._srv.votar(opcao)
            except Exception:
                pass
            self._votacao_aberta = False
            win.destroy()

        ctk.CTkButton(
            fb, text="✅  Continuar", width=145,
            command=lambda: _votar("continuar")).pack(
            side="left", padx=8)
        ctk.CTkButton(
            fb, text="🛑  Encerrar", width=145,
            fg_color="#c0392b", hover_color="#e74c3c",
            command=lambda: _votar("encerrar")).pack(
            side="left", padx=8)

        def _fechar_win():
            self._votacao_aberta = False
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", _fechar_win)

    def _fechar(self):
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
        self.destroy()


# ── Ponto de entrada

if __name__ == "__main__":
    App().mainloop()
