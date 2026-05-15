"""
cliente.py — Cliente do Jogo Multijogador (RPyC)
=================================================
Melhorias implementadas:
  - Turno atual exibido automaticamente no menu (sem opção manual separada)
  - Chat separado com histórico consultável
  - Menu numerado corrigido: opção 3 = Aceitar/Recusar troca (era 4)
  - Denúncia de espionagem como opção dedicada
  - Novo objeto / encerrar jogo
  - Feedback de pontuação detalhado
  - Relembrar objeto secreto próprio


"""

import rpyc
import threading
import sys


# Serviço do cliente — recebe notificações push do servidor


class ClienteService(rpyc.Service):
    """
    Recebe notificações assíncronas do servidor.
    Todos os métodos exposed_* rodam na thread do RPyC.
    """

    _print_lock = threading.Lock()

    def _print(self, *args, **kwargs):
        with ClienteService._print_lock:
            print(*args, **kwargs)

    # ── Notificações gerais

    def exposed_notificar_sistema(self, mensagem: str):
        """Mensagens de sistema: entradas, saídas, turnos, pontuação…"""
        self._print(f"\n  ⚙  {mensagem}")

    # ── Chat

    def exposed_receber_chat(self, remetente: str, texto: str):
        """Nova mensagem de chat PÚBLICO recebida em tempo real."""
        self._print(f"\n  [PÚBLICO] {remetente}: {texto}")

    def exposed_receber_chat_privado(self, remetente: str, texto: str):
        """Nova mensagem de chat PRIVADO recebida em tempo real."""
        self._print(f"\n  [PRIVADO] {remetente}: {texto}")

    # ── Dicas

    def exposed_receber_dica(self, remetente: str, dica: str):
        """Dica pública enviada por outro jogador."""
        self._print(f"\n  Dica de {remetente}: '{dica}'")

    # ── Trocas

    def exposed_notificar_troca_solicitada(self, solicitante: str):
        """
        O servidor avisa que 'solicitante' quer trocar dicas.
        O jogador deve usar a opção [3] do menu para responder.
        """
        self._print(
            f"\n  {solicitante} quer fazer uma TROCA DE DICAS com você!\n"
            f"     Use a opção [3] Aceitar/Recusar troca no menu."
        )

    # ── Espionagem

    def exposed_notificar_espionagem(self, espiao: str, pego: bool):
        """Notifica quando alguém foi detectado espiando."""
        if pego:
            self._print(
                f"\n  {espiao} foi DETECTADO espiando sua troca!\n"
                f"     Use a opção [8] Denunciar espião para puni-lo."
            )
        else:
            self._print(f"\n  Alerta: alguém pode estar espiando.")


# Helpers de terminal


def sep(char="─", width=52):
    print("\n" + char * width)


def exibir_turno(servidor, nome_local):
    """Exibe estado do lobby ou turno atual no cabeçalho do menu."""
    try:
        if not servidor.jogo_iniciado():
            jogadores = list(servidor.jogadores_lobby())
            print(
                f"\n  ⏳ LOBBY — Aguardando início | Jogadores: {', '.join(jogadores)}")
            return
        turno = servidor.turno_atual()
        rodada = servidor.rodada_atual()
        num_t = servidor.num_turno()
        marcador = " ← SUA VEZ" if turno == nome_local else ""
        print(
            f"\n  Rodada {rodada} | Turno {num_t} | Vez de: {turno}{marcador}")
    except Exception:
        pass


def menu_lobby(servidor, nome_local):
    """Menu exibido enquanto o jogo ainda não foi iniciado (lobby)."""
    exibir_turno(servidor, nome_local)
    sep()
    print("  LOBBY — SALA DE ESPERA")
    try:
        anfitriao = str(servidor.anfitriao())
    except Exception:
        anfitriao = ""
    if nome_local == anfitriao:
        print("  [1]  Iniciar partida (você é o anfitrião)")
    else:
        print(f"  [1]  Iniciar partida (apenas {anfitriao} pode iniciar)")
    print("  [2]  Ver jogadores no lobby")
    print("  [6]  Chat Público — enviar mensagem")
    print("  [6h] Chat Público — ver histórico")
    print("  [0]  Sair")
    sep()
    return input("  Escolha: ").strip()


def menu_jogo(servidor, nome_local):
    """Menu exibido durante a partida."""
    exibir_turno(servidor, nome_local)
    sep()
    print("  MENU DE AÇÕES")
    print("  ── DICAS ──────────────────────────")
    print("  [1]   Enviar dica pública")
    print("  [1h]  Visualizar dicas antigas")
    print("  ── TROCAS ─────────────────────────")
    print("  [2]   Solicitar troca privada de dicas")
    print("  [3]   Aceitar / Recusar troca pendente")
    print("  [4]   Espionar troca")
    print("  ── JOGO ───────────────────────────")
    print("  [5]   Fazer palpite")
    print("  [P]   Passar a vez (pular turno)")
    print("  ── CHAT ───────────────────────────")
    print("  [6]   Chat Público — enviar mensagem")
    print("  [6h]  Chat Público — ver histórico")
    print("  [6p]  Chat Privado — enviar mensagem")
    print("  [6ph] Chat Privado — ver histórico")
    print("  ── INFO ───────────────────────────")
    print("  [7]   Ver placar detalhado")
    print("  [8]   Denunciar espião")
    print("  [9]   Listar jogadores conectados")
    print("  [10]  Relembrar meu objeto secreto")
    print("  ── RODADA ─────────────────────────")
    print("  [0]   Sair")
    sep()
    return input("  Escolha: ").strip()


def menu_votacao(servidor):

    placar = dict(servidor.placar())

    sep()
    print("  FIM DA RODADA")
    sep()

    print("  PLACAR:")

    for jogador, pts in sorted(
        placar.items(),
        key=lambda x: -x[1]
    ):
        print(f"   {jogador}: {pts} pts")

    sep()

    print("  [1] Continuar jogo")
    print("  [2] Encerrar jogo")

    sep()

    return input("Escolha: ").strip()


# Ponto de entrada
if __name__ == "__main__":

    # ── Conecta ao servidor
    print("\n" + "=" * 52)
    print("  JOGO MULTIJOGADOR DE ADIVINHAÇÃO — RPyC")
    print("=" * 52)
    print("Conectando ao servidor em 127.0.0.1:18861…")
    try:
        conn = rpyc.connect(
            "127.0.0.1",
            18861,
            service=ClienteService,
            config={
                "allow_public_attrs": True,
                "allow_pickle": True,
                "sync_request_timeout": 300,
            },
        )
    except ConnectionRefusedError:
        print("ERRO: servidor não encontrado. Inicie servidor.py primeiro.")
        sys.exit(1)

    servidor = conn.root  # proxy para GameServer

    # Thread para processar callbacks do servidor em tempo real

    def processar_callbacks():
        while True:
            try:
                conn.serve(0.1)
            except EOFError:
                break
            except Exception:
                pass

    thread_callbacks = threading.Thread(
        target=processar_callbacks,
        daemon=True
    )

    thread_callbacks.start()

    # ── Registro
    nome = input("Seu nome no jogo: ").strip()
    resposta = servidor.entrar(nome)
    print(f"\n  {resposta}\n")

    if resposta.startswith("ERRO"):
        conn.close()
        sys.exit(1)

    # ──> Loop principal
    while True:

        try:

            # força atualização em tempo real
            em_jogo = bool(servidor.jogo_iniciado())

            # se existir votação ativa, considera como "em jogo"
            if servidor.votacao_ativa():
                em_jogo = True
            # ──> Votação: Votar para continuar a partida
            if servidor.votacao_ativa():

                acao = menu_votacao(servidor)

                if acao == "1":
                    print(
                        "  →",
                        servidor.votar("continuar")
                    )

                elif acao == "2":
                    print(
                        "  →",
                        servidor.votar("encerrar")
                    )

                else:
                    print("  Opção inválida.")

                continue
            em_jogo = servidor.jogo_iniciado()
            acao = menu_jogo(servidor, nome) if em_jogo else menu_lobby(
                servidor, nome)

            # ──> LOBBY: Iniciar partida
            if acao == "1" and not em_jogo:
                print("  →", servidor.iniciar_jogo())
                continue

            # ──> LOBBY: Ver jogadores
            if acao == "2" and not em_jogo:
                jogadores = list(servidor.jogadores_lobby())
                print(
                    f"  No lobby ({len(jogadores)}): {', '.join(jogadores) if jogadores else 'nenhum'}")
                continue

            # ──> Bloqueia ações de jogo se ainda no lobby
            # revalida imediatamente antes de bloquear
            if not bool(servidor.jogo_iniciado()) and not servidor.votacao_ativa():
                if acao not in ("0", "6", "6h"):
                    print("  ⚠  A partida ainda não foi iniciada. Aguarde o anfitrião.")
                    continue

            # ──> 1. Enviar dica
            if acao == "1":
                dica = input("  Digite sua dica (UMA palavra): ").strip()
                print("  →", servidor.enviar_dica(dica))

            # ──> 1h. Visualizar dicas antigas
            elif acao == "1h":
                dicas = list(servidor.historico_dicas())
                sep("─")
                print("  DICAS ENVIADAS NESTA RODADA:")
                if not dicas:
                    print("  (nenhuma dica enviada ainda)")
                else:
                    for remetente, dica, turno in dicas:
                        print(f"  Turno {turno:>3} | {remetente}: '{dica}'")
                sep("─")

            # ──> 2. Solicitar troca
            elif acao == "2":
                jogadores = list(servidor.jogadores())
                outros = [j for j in jogadores if j != nome]
                if not outros:
                    print("  Nenhum outro jogador conectado.")
                else:
                    print(f"  Jogadores disponíveis: {', '.join(outros)}")
                    alvo = input("  Trocar com quem? ").strip()
                    palavra = input(
                        "  Sua palavra para a troca (pode ser mentira): ").strip()
                    print("  →", servidor.solicitar_troca(alvo, palavra))

            # ──> 3. Aceitar/Recusar troca
            elif acao == "3":
                solicitante = input(
                    "  Nome de quem fez a solicitação: ").strip()
                decisao = input("  Aceitar? (s/n): ").strip().lower()
                if decisao == "s":
                    palavra = input(
                        "  Sua palavra para a troca (pode ser mentira): ").strip()
                    print("  →", servidor.aceitar_troca(solicitante, palavra))
                else:
                    print("  →", servidor.recusar_troca(solicitante))

            # ──> 4. Espionar
            elif acao == "4":
                jogadores = list(servidor.jogadores())
                outros = [j for j in jogadores if j != nome]
                if len(outros) < 2:
                    print(
                        "  São necessários pelo menos 2 outros jogadores para espionar.")
                else:
                    print(f"  Jogadores: {', '.join(outros)}")
                    ja = input("  Jogador A da troca: ").strip()
                    jb = input("  Jogador B da troca: ").strip()
                    print("  →", servidor.espionar(ja, jb))

            # ──> 5. Palpite
            elif acao == "5":
                jogadores = list(servidor.jogadores())
                outros = [j for j in jogadores if j != nome]
                if not outros:
                    print("  Nenhum alvo disponível.")
                else:
                    print(f"  Jogadores: {', '.join(outros)}")
                    alvo = input("  Adivinhar objeto de quem? ").strip()
                    chute = input("  Seu palpite: ").strip()
                    print("  →", servidor.palpite(alvo, chute))

            # ──> P. Passar a vez
            elif acao.lower() == "p":
                print("  →", servidor.passar_vez())

            # ──> 6. Chat Público — enviar
            elif acao == "6":
                msg = input("  [PÚBLICO] Sua mensagem: ").strip()
                if msg:
                    servidor.chat(msg)

            # ──> 6h. Chat Público — histórico
            elif acao == "6h":
                historico = list(servidor.historico_chat(20))
                sep("─")
                print("  CHAT PÚBLICO — últimas 20 mensagens:")
                if not historico:
                    print("  (nenhuma mensagem ainda)")
                else:
                    for remetente, texto in historico:
                        prefixo = "  ★ você" if remetente == nome else f"  {remetente}"
                        print(f"  {prefixo}: {texto}")
                sep("─")

            # ──> 6p. Chat Privado — enviar
            elif acao == "6p":
                jogadores = list(servidor.jogadores())
                outros = [j for j in jogadores if j != nome]
                if not outros:
                    print("  Nenhum outro jogador disponível.")
                else:
                    print(f"  Jogadores: {', '.join(outros)}")
                    dest = input("  Enviar para quem? ").strip()
                    msg = input("  [PRIVADO] Sua mensagem: ").strip()
                    if msg:
                        print("  →", servidor.chat_privado(dest, msg))

            # ──> 6ph. Chat Privado — histórico
            elif acao == "6ph":
                jogadores = list(servidor.jogadores())
                outros = [j for j in jogadores if j != nome]
                if not outros:
                    print("  Nenhum outro jogador disponível.")
                else:
                    print(f"  Jogadores: {', '.join(outros)}")
                    outro = input("  Ver histórico com quem? ").strip()
                    historico = list(
                        servidor.historico_chat_privado(outro, 20))
                    sep("─")
                    print(
                        f"  CHAT PRIVADO com {outro} — últimas 20 mensagens:")
                    if not historico:
                        print("  (nenhuma mensagem ainda)")
                    else:
                        for remetente, texto in historico:
                            prefixo = "  ★ você" if remetente == nome else f"  {remetente}"
                            print(f"  {prefixo}: {texto}")
                    sep("─")

            # ──> 7. Placar
            elif acao == "7":
                placar = dict(servidor.placar())
                sep("─")
                print("  PLACAR ATUAL:")
                medalhas = {1: "🥇", 2: "🥈", 3: "🥉"}
                for i, (jogador, pts) in enumerate(
                    sorted(placar.items(), key=lambda x: -x[1]), 1
                ):
                    icone = medalhas.get(i, f"  {i}.")
                    marcador = " ← você" if jogador == nome else ""
                    print(f"  {icone} {jogador}: {pts} pts{marcador}")
                sep("─")

            # ──> 8. Denunciar espião
            elif acao == "8":
                ha_espionagem = servidor.espionagens_pendentes()
                if not ha_espionagem:
                    print("  Nenhuma espionagem suspeita nesta rodada.")
                else:
                    print(
                        "  Você suspeita que alguém espionou sua conversa."
                    )

                    espiao = input(
                        "  Quem deseja denunciar? "
                    ).strip()

                    print(
                        "  →",
                        servidor.denunciar_espionagem(espiao)
                    )

            # ──> 9. Listar jogadores
            elif acao == "9":
                jogadores = list(servidor.jogadores())
                print(
                    f"  Conectados ({len(jogadores)}): "
                    f"{', '.join(jogadores) if jogadores else 'nenhum'}"
                )

            # ──> 10. Relembrar meu objeto
            elif acao == "10":
                resposta = servidor.meu_objeto()
                sep("─")
                print(str(resposta))
                sep("─")

            # ──> 0. Sair
            elif acao == "0":
                print("  Até logo!")
                try:
                    conn.close()
                except Exception:
                    pass
                break

            elif acao.strip():
                print("  Opção inválida. Digite um código do menu.")

        except EOFError:
            print("\n  Conexão encerrada pelo servidor.")
            break
        except KeyboardInterrupt:
            print("\n  Saindo…")
            try:
                conn.close()
            except Exception:
                pass
            break
        except Exception as e:
            print(f"\n  ERRO inesperado: {e}")
