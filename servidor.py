"""
servidor.py — Servidor Central do Jogo Multijogador (RPyC)
=============================================================
Arquitetura:
  - GameServer (rpyc.Service) expõe toda a lógica ao cliente.
  - Cada conexão cria uma instância de GameServer; o estado
    global fica em variáveis de CLASSE (compartilhadas).
  - O servidor notifica clientes de forma assíncrona chamando
    métodos exposed_* no conn.root (objeto do cliente).
"""

import rpyc
from rpyc.utils.server import ThreadedServer
import random
import threading


# Objetos secretos disponíveis para sorteio


OBJETOS_DISPONIVEIS = [
    "espada", "escudo", "poção", "mapa", "chave",
    "lanterna", "corda", "bússola", "cristal", "pergaminho",
    "amuleto", "tocha", "cajado", "elmo", "flecha",
]



# Constantes de pontuação


PTS_PRIMEIRO_ACERTO = 5
PTS_ACERTO_POSTERIOR = 3
PTS_BONUS_UNICO_ACERTO = 2
PTS_DONO_SO_UM_ACERTO = 2
PTS_DONO_TODOS_ACERT = -1
PTS_ESPIOU_PEGO = -2
TURNOS_POR_JOGADOR = 3


# Estado Global (nível de classe — compartilhado entre todas as conexões)


class GameServer(rpyc.Service):
    """Servidor central: gerencia jogadores, turnos, pontuação e chat."""

    _lock = threading.Lock()

    # NOVO — GUARDA A CONEXÃO DO CLIENTE

    def on_connect(self, conn):
        """
        Chamado automaticamente quando um cliente conecta.
        Guarda a conexão para callbacks.
        """
        self._conn = conn
        self._nome = None

    # ESTADO GLOBAL

    _jogadores = {}

    _turno_atual = None
    _turno_idx = 0
    _ordem_turnos = []
    _num_turno = 0
    _rodada = 1

    _trocas_pendentes = {}
    _trocas_realizadas = []

    _espionagens_rodada = {}
    _espionagem_chat = []

    _historico_chat_publico = []
    _historico_chat_privado = {}

    _historico_dicas = []

    _jogo_iniciado = False
    _votacao_ativa = False
    _votos = {}
    _eventos_pendentes = []

    _anfitriao = None

    def on_disconnect(self, conn):
        nome_saiu = self._nome
        if nome_saiu:
            with GameServer._lock:
                GameServer._jogadores.pop(nome_saiu, None)
                if nome_saiu in GameServer._ordem_turnos:
                    GameServer._ordem_turnos.remove(nome_saiu)
                    if GameServer._ordem_turnos:
                        GameServer._turno_idx %= len(GameServer._ordem_turnos)
                self._atualizar_turno()
            # FORA DO LOCK
            self._broadcast_sistema(f"{nome_saiu} saiu do jogo.")

    # ──> Helpers internos

    def _cliente(self, nome):
        info = GameServer._jogadores.get(nome)
        if info:
            try:
                return info["conn"].root
            except Exception:
                pass
        return None

    def _broadcast_sistema(self, mensagem: str):
        """Envia notificação de sistema a todos os jogadores."""
        for nome, info in list(GameServer._jogadores.items()):
            try:
                rpyc.async_(info["conn"].root.notificar_sistema)(mensagem)
            except Exception:
                pass

    def _broadcast_exceto(self, excluido: str, mensagem: str):
        for nome, info in list(GameServer._jogadores.items()):
            if nome == excluido:
                continue
            try:
                rpyc.async_(info["conn"].root.notificar_sistema)(mensagem)
            except Exception:
                pass

    def _notificar_um(self, nome: str, mensagem: str):
        cli = self._cliente(nome)
        if cli:
            try:
                rpyc.async_(cli.notificar_sistema)(mensagem)
            except Exception:
                pass

    def _atualizar_turno(self):
        """Recalcula quem é o próximo na ordem de turnos (sem broadcast)."""
        ordem = GameServer._ordem_turnos
        if not ordem:
            GameServer._turno_atual = None
            return
        GameServer._turno_idx %= len(ordem)
        GameServer._turno_atual = ordem[GameServer._turno_idx]

    def _avancar_turno_interno(self):
        """
        Avança o turno internamente.
        ATENÇÃO: NÃO deve ser chamado com o lock ativo, pois
        _broadcast_sistema() também precisa iterar os jogadores.
        """
        ordem = GameServer._ordem_turnos
        if not ordem:
            return
        with GameServer._lock:
            GameServer._turno_idx = (GameServer._turno_idx + 1) % len(ordem)
            GameServer._turno_atual = ordem[GameServer._turno_idx]
            GameServer._num_turno += 1
            turno_num = GameServer._num_turno
            turno_nome = GameServer._turno_atual
            rodada = GameServer._rodada

        # primeiro anuncia o turno
        self._broadcast_sistema(
            f"[TURNO {turno_num}] Agora é a vez de: {turno_nome} "
            f"(Rodada {rodada})"
        )
        # ENTREGA ESPIONAGENS PENDENTES AO ESPIÃO
        with GameServer._lock:

            registros = GameServer._espionagens_rodada.get(rodada, [])

        for reg in registros:

            if reg["espiao"] != turno_nome:
                continue

            if not reg["mensagens"]:
                continue

            espiao_cli = self._cliente(turno_nome)

            if not espiao_cli:
                continue

            for msg in reg["mensagens"]:

                if msg["tipo"] == "troca":

                    try:

                        rpyc.async_(espiao_cli.notificar_sistema)(
                            "[ESPIONAGEM]\n"
                            f"Você espionou a troca entre "
                            f"{msg['a']} e {msg['b']}!\n"
                            f"{msg['a']} disse: '{msg['pa']}'\n"
                            f"{msg['b']} disse: '{msg['pb']}'"
                        )

                    except Exception:
                        pass

            reg["mensagens"].clear()

        # MOSTRA EVENTOS SOMENTE QUANDO O CICLO REINICIA
        primeiro_jogador = GameServer._ordem_turnos[0]

        if turno_nome == primeiro_jogador:

            # eventos de troca
            with GameServer._lock:

                eventos = list(GameServer._eventos_pendentes)

                GameServer._eventos_pendentes.clear()

            for evento in eventos:
                self._broadcast_sistema(evento)

            # eventos de espionagem
            with GameServer._lock:

                houve_espiao = any(
                    not reg["avisado"]
                    for regs in GameServer._espionagens_rodada.values()
                    for reg in regs
                )

                if houve_espiao:

                    for regs in GameServer._espionagens_rodada.values():
                        for reg in regs:
                            reg["avisado"] = True

            if houve_espiao:
                self._broadcast_sistema(
                    "[ESPIONAGEM] Houve um espião na rodada."
                )
        max_turnos = len(GameServer._jogadores) * TURNOS_POR_JOGADOR

        if turno_num >= max_turnos:

            GameServer._jogo_iniciado = False
            GameServer._votacao_ativa = True
            GameServer._votos = {}

            self._broadcast_sistema(
                "[FIM DA RODADA] Limite de turnos atingido!\n"
                "Todos devem votar para continuar ou encerrar o jogo."
            )
    # ──> Entrar no jogo

    def exposed_entrar(self, nome: str) -> str:
        nome = str(nome).strip()
        if not nome:
            return "ERRO: nome inválido."

        with GameServer._lock:
            if nome in GameServer._jogadores:
                return f"ERRO: nome '{nome}' já está em uso."

            GameServer._jogadores[nome] = {
                "conn":        self._conn,
                "objeto":      None,
                "pontos":      0,
                "acertadores": set(),
                "trocas_usadas": 0,
            }
            GameServer._ordem_turnos.append(nome)

            # FIX: self._nome é definido aqui, dentro do lock, garantindo
            # que on_disconnect sempre encontra o nome correto.
            self._nome = nome

            if GameServer._anfitriao is None:
                GameServer._anfitriao = nome

        self._broadcast_exceto(nome, f"{nome} entrou no lobby!")

        jogadores_presentes = list(GameServer._jogadores.keys())
        papel = (
            "Você é o ANFITRIÃO — use [1] para iniciar a partida."
            if nome == GameServer._anfitriao
            else f"Aguarde o anfitrião ({GameServer._anfitriao}) iniciar a partida."
        )
        return (
            f"Bem-vindo ao lobby, {nome}! "
            f"Jogadores aguardando: {', '.join(jogadores_presentes)}. "
            f"{papel}"
        )

    # ──> Lobby: Iniciar Partida

    def exposed_iniciar_jogo(self) -> str:
        if not self._nome:
            return "ERRO: entre no lobby primeiro."
        if self._nome != GameServer._anfitriao:
            return (
                f"ERRO: apenas o anfitrião "
                f"({GameServer._anfitriao}) pode iniciar a partida."
            )

        notificacoes = []

        with GameServer._lock:
            if GameServer._jogo_iniciado:
                return "ERRO: a partida já foi iniciada."
            if len(GameServer._jogadores) < 2:
                return "ERRO: são necessários pelo menos 2 jogadores."

            GameServer._jogo_iniciado = True
            usados = set()
            for nome, info in GameServer._jogadores.items():
                disponiveis = [
                    o for o in OBJETOS_DISPONIVEIS if o not in usados]
                if not disponiveis:
                    disponiveis = OBJETOS_DISPONIVEIS
                obj = random.choice(disponiveis)
                usados.add(obj)
                info["objeto"] = obj
                notificacoes.append((
                    info["conn"],
                    f"[INÍCIO] A partida começou!\n"
                    f"Seu objeto secreto é: [{obj.upper()}]\n"
                    f"Guarde bem seu objeto!"
                ))
            GameServer._turno_idx = 0
            GameServer._turno_atual = GameServer._ordem_turnos[0]

        for conn, msg in notificacoes:
            try:
                rpyc.async_(conn.root.notificar_sistema)(msg)
            except Exception:
                pass

        self._broadcast_sistema(
            f"[INÍCIO] Partida iniciada! "
            f"Primeiro turno: {GameServer._turno_atual}"
        )
        return f"Partida iniciada! Primeiro turno: {GameServer._turno_atual}"

    def exposed_jogo_iniciado(self) -> bool:
        return GameServer._jogo_iniciado

    def exposed_anfitriao(self) -> str:
        return GameServer._anfitriao or ""

    def exposed_jogadores_lobby(self) -> list:
        return list(GameServer._jogadores.keys())

    def exposed_votacao_ativa(self):
        return GameServer._votacao_ativa

    def exposed_votar(self, voto):

        if not GameServer._votacao_ativa:
            return "ERRO: não há votação ativa."

        voto = str(voto).lower()

        if voto not in ("continuar", "encerrar"):
            return "ERRO: voto inválido."

        with GameServer._lock:

            GameServer._votos[self._nome] = voto

            total = len(GameServer._jogadores)

            self._broadcast_sistema(
                f"{self._nome} votou."
            )

            # Todos votaram?
            if len(GameServer._votos) == total:

                continuar = list(
                    GameServer._votos.values()
                ).count("continuar")

                encerrar = list(
                    GameServer._votos.values()
                ).count("encerrar")

                GameServer._votacao_ativa = False

            
                # CONTINUAR JOGO
               
                if continuar > encerrar:

                    GameServer._rodada += 1
                    GameServer._num_turno = 0
                    GameServer._jogo_iniciado = True

                    GameServer._espionagens_rodada = {}
                    GameServer._trocas_pendentes = {}
                    GameServer._historico_dicas = []

                    usados = set()

                    for nome, info in GameServer._jogadores.items():

                        disponiveis = [
                            o for o in OBJETOS_DISPONIVEIS
                            if o not in usados
                        ]

                        if not disponiveis:
                            disponiveis = OBJETOS_DISPONIVEIS

                        obj = random.choice(disponiveis)

                        usados.add(obj)

                        info["objeto"] = obj
                        info["acertadores"] = set()
                        info["trocas_usadas"] = False

                        try:
                            rpyc.async_(
                                info["conn"].root.notificar_sistema
                            )(
                                f"[NOVA RODADA {GameServer._rodada}]\n"
                                f"Seu objeto secreto é: [{obj.upper()}]"
                            )
                        except Exception:
                            pass

                    GameServer._turno_idx = 0

                    GameServer._turno_atual = (
                        GameServer._ordem_turnos[0]
                        if GameServer._ordem_turnos
                        else None
                    )

                    self._broadcast_sistema(
                        f"[VOTAÇÃO] A maioria decidiu continuar!\n"
                        f"Nova rodada iniciada!\n"
                        f"Primeiro turno: {GameServer._turno_atual}"
                    )

                    return "Nova rodada iniciada."


                # ENCERRAR JOGO

                else:

                    GameServer._jogo_iniciado = False

                    self._broadcast_sistema(
                        "[VOTAÇÃO] A maioria decidiu encerrar o jogo!"
                    )

                    return "Jogo encerrado."

        return "Voto registrado. Aguardando outros jogadores."

    # ──> 1. Enviar Dica

    def exposed_enviar_dica(self, dica: str) -> str:
        if not self._nome:
            return "ERRO: entre no jogo primeiro."
        if not GameServer._jogo_iniciado:
            return "ERRO: a partida ainda não foi iniciada. Aguarde o início no lobby."
        if GameServer._turno_atual and GameServer._turno_atual != self._nome:
            return f"ERRO: não é sua vez. Aguarde o turno de {GameServer._turno_atual}."

        dica = str(dica).strip()
        if not dica:
            return "ERRO: dica não pode ser vazia."
        if len(dica.split()) > 1:
            return "ERRO: a dica deve ser UMA única palavra."

        with GameServer._lock:
            GameServer._historico_dicas.append(
                (self._nome, dica, GameServer._num_turno)
            )

        for nome, info in list(GameServer._jogadores.items()):
            if nome != self._nome:
                try:
                    rpyc.async_(info["conn"].root.receber_dica)(
                        self._nome, dica)
                except Exception:
                    pass

        # _avancar_turno_interno chamado FORA do lock para evitar
        # deadlock causado pelo broadcast interno ao método.
        self._avancar_turno_interno()

        return f"Dica '{dica}' enviada a todos. Turno avançado automaticamente."

    def exposed_historico_dicas(self) -> list:
        with GameServer._lock:
            return list(GameServer._historico_dicas)

    # ──> 2. Solicitar Troca Privada

    def exposed_solicitar_troca(self, alvo: str, minha_palavra: str) -> str:
        if not self._nome:
            return "ERRO: entre no jogo primeiro."
        if alvo not in GameServer._jogadores:
            return f"ERRO: jogador '{alvo}' não encontrado."
        if alvo == self._nome:
            return "ERRO: você não pode trocar com você mesmo."

        info_self = GameServer._jogadores.get(self._nome, {})
        if info_self.get("trocas_usadas", 0) >= 3:
            return "ERRO: você já usou suas 3 trocas privadas nesta rodada."

        with GameServer._lock:
            GameServer._trocas_pendentes[self._nome] = {
                "alvo":    alvo,
                "palavra": str(minha_palavra).strip(),
            }

        cli_alvo = self._cliente(alvo)
        if cli_alvo:
            try:
                rpyc.async_(cli_alvo.notificar_troca_solicitada)(self._nome)
            except Exception:
                pass

        return f"Solicitação de troca enviada a {alvo}. Aguarde resposta."

    def exposed_aceitar_troca(self, solicitante: str, minha_palavra: str) -> str:
        if not self._nome:
            return "ERRO: entre no jogo primeiro."

        with GameServer._lock:
            pendente = GameServer._trocas_pendentes.get(solicitante)
            if not pendente or pendente["alvo"] != self._nome:
                return f"ERRO: nenhuma troca pendente de '{solicitante}' para você."

            palavra_a = pendente["palavra"]
            palavra_b = str(minha_palavra).strip()

            GameServer._trocas_realizadas.append({
                "a":      solicitante,
                "b":      self._nome,
                "pa":     palavra_a,
                "pb":     palavra_b,
                "rodada": GameServer._rodada,
            })

            # ENTREGA PARA ESPIÕES
            for reg in GameServer._espionagens_rodada.get(GameServer._rodada, []):

                envolvidos = {reg["alvo_a"], reg["alvo_b"]}
                troca_real = {solicitante, self._nome}

                # espionagem correta
                if envolvidos == troca_real:

                    reg["mensagens"].append({
                        "tipo": "troca",
                        "a": solicitante,
                        "b": self._nome,
                        "pa": palavra_a,
                        "pb": palavra_b,
                    })

                    espiao_cli = self._cliente(reg["espiao"])

                    if espiao_cli:
                        try:

                            rpyc.async_(espiao_cli.notificar_sistema)(
                                "[ESPIONAGEM]\n"
                                f"Você espionou a troca entre "
                                f"{solicitante} e {self._nome}!\n"
                                f"{solicitante} disse: '{palavra_a}'\n"
                                f"{self._nome} disse: '{palavra_b}'"
                            )

                        except Exception:
                            pass

            del GameServer._trocas_pendentes[solicitante]
            GameServer._jogadores[solicitante]["trocas_usadas"] += 1
            GameServer._jogadores[self._nome]["trocas_usadas"] += 1

        self._notificar_um(
            solicitante,
            f"[TROCA] {self._nome} aceitou. Palavra recebida: '{palavra_b}'"
        )

        GameServer._eventos_pendentes.append(
            "[TROCA] Dois jogadores realizaram uma troca privada de dicas nesta rodada."
        )

        return f"Troca concluída! Você recebeu a palavra: '{palavra_a}'"

    def exposed_recusar_troca(self, solicitante: str) -> str:
        if not self._nome:
            return "ERRO: entre no jogo primeiro."
        with GameServer._lock:
            pendente = GameServer._trocas_pendentes.get(solicitante)
            if not pendente or pendente["alvo"] != self._nome:
                return "ERRO: nenhuma troca pendente para recusar."
            del GameServer._trocas_pendentes[solicitante]

        self._notificar_um(
            solicitante, f"[TROCA] {self._nome} recusou sua solicitação.")
        return "Troca recusada."

    # ──> 3. Espionagem

    def exposed_espionar(self, jogador_a: str, jogador_b: str) -> str:

        if not self._nome:
            return "ERRO: entre no jogo primeiro."

        if self._nome in (jogador_a, jogador_b):
            return "ERRO: você não pode espionar sua própria conversa."

        with GameServer._lock:

            rodada = GameServer._rodada

            if rodada not in GameServer._espionagens_rodada:
                GameServer._espionagens_rodada[rodada] = []

            GameServer._espionagens_rodada[rodada].append({

                "espiao": self._nome,

                "alvo_a": jogador_a,
                "alvo_b": jogador_b,

                "mensagens": [],

                "avisado": False,
                "denunciado": False,

                "turno_detectar":
                    GameServer._num_turno + len(GameServer._jogadores)
            })

        return (
            "[ESPIONAGEM] Espionagem registrada.\n"
            "Aguarde até seu próximo turno."
        )
    def exposed_denunciar_espionagem(self, espiao: str) -> str:

        if not self._nome:
            return "ERRO: entre no jogo primeiro."

        rodada = GameServer._rodada
        # libera espionagens após uma volta completa
        registros = GameServer._espionagens_rodada.get(rodada, [])

        for reg in registros:

            if (
                not reg["avisado"]
                and GameServer._num_turno >= reg["turno_detectar"]
            ):

                reg["avisado"] = True

                self._broadcast_sistema(
                    "[ESPIONAGEM] Alguém espionou uma conversa na rodada passada."
                )

        with GameServer._lock:

            registros = GameServer._espionagens_rodada.get(rodada, [])
            alvo_registro = None

            for reg in registros:
                if (
                    reg["espiao"] == espiao
                    and self._nome in (reg["alvo_a"], reg["alvo_b"])
                    and not reg["denunciado"]
                ):
                    alvo_registro = reg
                    break

            if alvo_registro:
                alvo_registro["denunciado"] = True
                recompensa = 4
                if espiao in GameServer._jogadores:
                    GameServer._jogadores[espiao]["pontos"] += PTS_ESPIOU_PEGO
                    GameServer._jogadores[self._nome]["pontos"] += recompensa
                    pontos_atuais = GameServer._jogadores[espiao]["pontos"]
                    pontos_denunciante = GameServer._jogadores[self._nome]["pontos"]
                else:
                    pontos_atuais = "?"
                    pontos_denunciante = "?"

        # ERROU A DENÚNCIA
        if not alvo_registro:
            self._broadcast_sistema(
                f"[DENÚNCIA] {self._nome} acusou {espiao}, "
                f"mas não havia provas de espionagem."
            )
            self._avancar_turno_interno()
            return f"Você denunciou {espiao}, mas ele não espionou sua conversa."

        # DENÚNCIA CORRETA
        self._broadcast_sistema(
            f"[DENÚNCIA] {self._nome} descobriu o espião {espiao}! "
            f"{espiao} perde -{abs(PTS_ESPIOU_PEGO)} pts "
            f"e {self._nome} ganha +4 pts."
        )
        self._notificar_um(
            espiao,
            f"[PUNIÇÃO] Você foi descoberto espionando "
            f"{self._nome}! "
            f"{PTS_ESPIOU_PEGO} pts. "
            f"Total: {pontos_atuais}"
        )
        self._avancar_turno_interno()

        return (
            f"Você descobriu o espião {espiao}! "
            f"Ele perdeu {abs(PTS_ESPIOU_PEGO)} pts "
            f"e você ganhou +4 pts.\n"
            f"Seu total agora é: {pontos_denunciante} pts."
        )

    # ── 4. Palpite

    def exposed_palpite(self, alvo: str, chute: str) -> str:
        if not self._nome:
            return "ERRO: entre no jogo primeiro."
        if alvo not in GameServer._jogadores:
            return f"ERRO: jogador '{alvo}' não encontrado."
        if alvo == self._nome:
            return "ERRO: você não pode adivinhar seu próprio objeto."

        objeto_real = GameServer._jogadores[alvo]["objeto"]
        acertou = chute.strip().lower() == objeto_real.lower()

        if not acertou:
            self._avancar_turno_interno()
            return f"Errou! '{chute}' não é o objeto de {alvo}. Continue tentando!"

        with GameServer._lock:
            jogadores_list = list(GameServer._jogadores.keys())
            adversarios = [
                n for n in jogadores_list if n not in (self._nome, alvo)]

            acertadores = GameServer._jogadores[alvo].get("acertadores", set())
            if self._nome in acertadores:
                return "ERRO: você já acertou este objeto."

            primeiro = len(acertadores) == 0
            if primeiro:
                pontos_adivinhador = PTS_PRIMEIRO_ACERTO
                motivo_adivinhador = f"+{PTS_PRIMEIRO_ACERTO} pts (PRIMEIRO a adivinhar!)"
            else:
                pontos_adivinhador = PTS_ACERTO_POSTERIOR
                motivo_adivinhador = f"+{PTS_ACERTO_POSTERIOR} pts (acerto posterior)"

            acertadores.add(self._nome)
            GameServer._jogadores[alvo]["acertadores"] = acertadores
            GameServer._jogadores[self._nome]["pontos"] += pontos_adivinhador
            total_adivinhador = GameServer._jogadores[self._nome]["pontos"]

            # FIX: condição corrigida de "> 1" para ">= 1" para funcionar
            # corretamente em partidas com apenas 2 jogadores.
            todos_acertaram = (
                acertadores >= set(adversarios) and len(adversarios) >= 1
            )
            unico_acerto = len(acertadores) == 1

            if todos_acertaram and not unico_acerto:
                GameServer._jogadores[alvo]["pontos"] += PTS_DONO_TODOS_ACERT
                msg_dono = (
                    f"[OBJETO] {alvo}: {PTS_DONO_TODOS_ACERT} pt "
                    f"(todos adivinharam — dica fácil demais!)"
                )
            elif unico_acerto:
                GameServer._jogadores[alvo]["pontos"] += PTS_DONO_SO_UM_ACERTO
                msg_dono = (
                    f"[OBJETO] {alvo}: +{PTS_DONO_SO_UM_ACERTO} pts "
                    f"(apenas 1 jogador adivinhou seu objeto — bom segredo!)"
                )
            else:
                msg_dono = f"[OBJETO] {alvo}: sem ajuste de pontos por agora."

        self._notificar_um(
            alvo,
            f"[PALPITE] {self._nome} adivinhou seu objeto '{objeto_real}'! {msg_dono}"
        )
        self._broadcast_exceto(
            self._nome,
            f"[PALPITE] {self._nome} adivinhou o objeto de {alvo}!"
        )
        self._avancar_turno_interno()

        return (
            f"ACERTOU! O objeto de {alvo} é '{objeto_real}'. "
            f"{motivo_adivinhador}. "
            f"Seu total agora: {total_adivinhador} pts."
        )

    # ──> 5. Chat

    def exposed_chat(self, mensagem: str) -> str:
        if not self._nome:
            return "ERRO: entre no jogo primeiro."
        texto = str(mensagem).strip()
        if not texto:
            return "ERRO: mensagem vazia."

        with GameServer._lock:
            GameServer._historico_chat_publico.append((self._nome, texto))
            if len(GameServer._historico_chat_publico) > 100:
                GameServer._historico_chat_publico = \
                    GameServer._historico_chat_publico[-100:]

        for nome, info in list(GameServer._jogadores.items()):
            try:
                info["conn"].root.receber_chat(self._nome, texto)
            except Exception:
                pass

        return "✓ Mensagem pública enviada."

    def exposed_chat_privado(self, destinatario: str, mensagem: str) -> str:
        if not self._nome:
            return "ERRO: entre no jogo primeiro."
        if destinatario not in GameServer._jogadores:
            return f"ERRO: jogador '{destinatario}' não encontrado."
        if destinatario == self._nome:
            return "ERRO: você não pode enviar mensagem privada para si mesmo."
        texto = str(mensagem).strip()
        if not texto:
            return "ERRO: mensagem vazia."

        chave = tuple(sorted([self._nome, destinatario]))
        with GameServer._lock:
            if chave not in GameServer._historico_chat_privado:
                GameServer._historico_chat_privado[chave] = []
            GameServer._historico_chat_privado[chave].append(
                (self._nome, texto))
            if len(GameServer._historico_chat_privado[chave]) > 100:
                GameServer._historico_chat_privado[chave] = \
                    GameServer._historico_chat_privado[chave][-100:]
        
        # ENTREGA PARA ESPIÕES
        for rodada, registros in GameServer._espionagens_rodada.items():

            if rodada != GameServer._rodada:
                continue

            for reg in registros:

                if reg["espiao"] == self._nome:
                    continue

                envolvidos = {reg["alvo_a"], reg["alvo_b"]}

                if {self._nome, destinatario} == envolvidos:

                    espiao_cli = self._cliente(reg["espiao"])

                    if espiao_cli:
                        try:
                            # ENTREGA PARA ESPIÕES
                            for rodada, registros in GameServer._espionagens_rodada.items():

                                if rodada != GameServer._rodada:
                                    continue

                                for reg in registros:

                                    if reg["espiao"] == self._nome:
                                        continue

                                    envolvidos = {reg["alvo_a"], reg["alvo_b"]}

                                    # conversa espionada encontrada
                                    if {self._nome, destinatario} == envolvidos:

                                        # salva no histórico da espionagem
                                        reg["mensagens"].append({
                                            "de": self._nome,
                                            "para": destinatario,
                                            "texto": texto
                                        })

                                        espiao_cli = self._cliente(reg["espiao"])

                                        if espiao_cli:
                                            try:

                                                rpyc.async_(espiao_cli.receber_chat_privado)(
                                                    f"ESPIANDO {self._nome}",
                                                    f"[para {destinatario}] {texto}"
                                                )

                                            except Exception:
                                                pass
                        except Exception:
                            pass
        cli = self._cliente(destinatario)
        if cli:
            try:
                cli.receber_chat_privado(self._nome, texto)
            except Exception:
                pass

        return f"✓ Mensagem privada enviada para {destinatario}."

    def exposed_historico_chat(self, ultimas: int = 20) -> list:
        with GameServer._lock:
            return list(GameServer._historico_chat_publico[-int(ultimas):])

    def exposed_historico_chat_privado(self, outro: str, ultimas: int = 20) -> list:
        chave = tuple(sorted([self._nome, outro]))
        with GameServer._lock:
            return list(
                GameServer._historico_chat_privado.get(
                    chave, [])[-int(ultimas):]
            )

    # ──> 6. Novo Objeto / Nova Rodada

    def exposed_novo_objeto(self) -> str:
        """Inicia uma NOVA rodada após o encerramento da rodada atual."""
        # FIX: validações na ordem correta; docstring antes das guardas.
        if not self._nome:
            return "ERRO: entre no jogo primeiro."
        if self._nome != GameServer._anfitriao:
            return (
                f"ERRO: apenas o anfitrião "
                f"({GameServer._anfitriao}) pode iniciar nova rodada."
            )
        if GameServer._jogo_iniciado:
            return (
                "ERRO: a rodada atual ainda está em andamento. "
                "Encerre o jogo primeiro com a opção [Encerrar Jogo]."
            )

        with GameServer._lock:
            GameServer._rodada += 1
            GameServer._num_turno = 0
            GameServer._jogo_iniciado = True
            GameServer._espionagens_rodada = {}
            GameServer._trocas_pendentes = {}
            GameServer._historico_dicas = []

            usados = set()
            for nome, info in GameServer._jogadores.items():
                disponiveis = [
                    o for o in OBJETOS_DISPONIVEIS if o not in usados]
                if not disponiveis:
                    disponiveis = OBJETOS_DISPONIVEIS
                obj = random.choice(disponiveis)
                usados.add(obj)
                info["objeto"] = obj
                info["acertadores"] = set()
                info["trocas_usadas"] = 0
                try:
                    rpyc.async_(info["conn"].root.notificar_sistema)(
                        f"[NOVA RODADA {GameServer._rodada}]\n"
                        f"Seu objeto secreto é: [{obj.upper()}]"
                    )
                except Exception:
                    pass

            GameServer._turno_idx = 0
            GameServer._turno_atual = (
                GameServer._ordem_turnos[0]
                if GameServer._ordem_turnos else None
            )

        self._broadcast_sistema(
            f"[NOVA RODADA] Rodada {GameServer._rodada} iniciada! "
            f"Todos receberam novos objetos. Primeiro turno: {GameServer._turno_atual}"
        )
        return f"Nova rodada {GameServer._rodada} iniciada com sucesso!"

    def exposed_encerrar_jogo(self) -> str:
        """Encerra a rodada atual, exibe placar final e libera nova rodada."""
        # FIX: docstring antes das guardas.
        if not self._nome:
            return "ERRO: entre no jogo primeiro."
        if self._nome != GameServer._anfitriao:
            return (
                f"ERRO: apenas o anfitrião "
                f"({GameServer._anfitriao}) pode encerrar."
            )

        with GameServer._lock:
            GameServer._jogo_iniciado = False

            bonificados = []
            for dono_nome, dono_info in GameServer._jogadores.items():
                acertadores = dono_info.get("acertadores", set())
                if len(acertadores) == 1:
                    unico = next(iter(acertadores))
                    if unico in GameServer._jogadores:
                        GameServer._jogadores[unico]["pontos"] += PTS_BONUS_UNICO_ACERTO
                        bonificados.append((unico, dono_nome))

            placar = sorted(
                GameServer._jogadores.items(),
                key=lambda x: -x[1]["pontos"]
            )

        medalhas = ["🥇", "🥈", "🥉"]
        linhas = ["[FIM DE JOGO] Placar Final:"]
        if bonificados:
            for adiv, dono in bonificados:
                linhas.append(
                    f"  ★ Bônus único: {adiv} foi o único a adivinhar o objeto de {dono} "
                    f"(+{PTS_BONUS_UNICO_ACERTO} pts)"
                )
        for i, (nome, info) in enumerate(placar, 1):
            icone = medalhas[i - 1] if i <= 3 else f"  {i}."
            linhas.append(f"  {icone} {nome}: {info['pontos']} pts")
        linhas.append(
            "Use [Nova Rodada] para jogar novamente ou [Sair] para encerrar."
        )

        resultado = "\n".join(linhas)
        self._broadcast_sistema(resultado)
        return resultado

    # ──> Controle de Turno

    def exposed_passar_vez(self) -> str:
        if not self._nome:
            return "ERRO: entre no jogo primeiro."
        if not GameServer._jogo_iniciado:
            return "ERRO: a partida ainda não foi iniciada."
        if GameServer._turno_atual != self._nome:
            return (
                f"ERRO: não é sua vez. "
                f"Aguarde {GameServer._turno_atual}."
            )

        self._avancar_turno_interno()
        self._broadcast_sistema(f"[TURNO] {self._nome} passou a vez.")
        return f"Você passou a vez. Agora é a vez de: {GameServer._turno_atual}"

    def exposed_avancar_turno(self) -> str:
        """Avança o turno manualmente (compatibilidade)."""
        self._avancar_turno_interno()
        return f"Turno avançado. Agora: {GameServer._turno_atual}"

    # ──> Consultas

    def exposed_placar(self) -> dict:
        with GameServer._lock:
            return {n: v["pontos"] for n, v in GameServer._jogadores.items()}

    def exposed_jogadores(self) -> list:
        return list(GameServer._jogadores.keys())

    def exposed_turno_atual(self) -> str:
        return GameServer._turno_atual or "nenhum"

    def exposed_rodada_atual(self) -> int:
        return GameServer._rodada

    def exposed_num_turno(self) -> int:
        return GameServer._num_turno

    def exposed_meu_objeto(self) -> str:
        if not self._nome:
            return "ERRO: não registrado."
        with GameServer._lock:
            if not GameServer._jogo_iniciado:
                return "Os objetos ainda não foram distribuídos (a partida não foi iniciada)."
            info = GameServer._jogadores.get(self._nome)
            if not info:
                return "ERRO: jogador não encontrado."
            obj = info["objeto"]
        if obj is None:
            return "Objeto ainda não atribuído."
        return f"Seu objeto secreto: [{obj.upper()}]"

    def exposed_espionagens_pendentes(self) -> bool:

        if not self._nome:
            return False

        rodada = GameServer._rodada

        for reg in GameServer._espionagens_rodada.get(rodada, []):

            if (
                self._nome in (reg["alvo_a"], reg["alvo_b"])
                and reg["avisado"]
                and not reg["denunciado"]
            ):

                return True

        return False


# Ponto de entrada

if __name__ == "__main__":
    print("=" * 55)
    print("  Servidor do Jogo Multijogador — RPyC")
    print("  Porta: 18861")
    print("=" * 55)
    servidor = ThreadedServer(
        GameServer,
        hostname="127.0.0.1",
        port=18861,
        protocol_config={
            "allow_public_attrs": True,
            "allow_pickle":       True,
        },
    )
    servidor.start()
