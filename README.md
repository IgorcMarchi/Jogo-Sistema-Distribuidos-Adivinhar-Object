# Avaliação Prática 01 – RPC/RMI
## Jogo Multijogador de Adivinhação de Objetos

**Disciplina:** Sistemas Distribuídos e Tecnologias (CC5SDT)  
**Semestre:** 2026-1  
**Professor:** Rafael Keller Tesser  
**Linguagem:** Python 3  
**Biblioteca RPC:** RPyC (Remote Python Call)

---

## 1. Introdução à Biblioteca RPyC

### O que é RPyC?

RPyC é uma biblioteca de Python que permite fazer chamadas remotas de forma bem simples. A ideia principal dela é que você consegue chamar funções que estão rodando em outro computador (ou em outro processo) praticamente da mesma forma que chamaria funções locais. O nome significa "Remote Python Call", ou seja, chamada remota em Python.

Ela funciona seguindo o modelo RPC (Remote Procedure Call): um lado expõe funções e o outro lado pode chamá-las como se fossem locais. No RPyC, qualquer método que começa com `exposed_` fica disponível para clientes remotos chamarem.

Um diferencial importante do RPyC é que ele suporta comunicação bidirecional: não só o cliente chama o servidor, mas o servidor também pode chamar funções que estão no cliente. Isso é chamado de "callback assíncrono", e foi exatamente o que usamos para notificar os jogadores em tempo real sem precisar de polling.

### Por que escolhemos o RPyC?

Consideramos três opções principais: XML-RPC (que já vem no Python), gRPC (do Google) e RPyC.

- **XML-RPC** é simples mas bem limitado: não tem suporte nativo a callbacks e os tipos de dados que podem ser enviados são bem restritos.
- **gRPC** é muito poderoso e eficiente, mas exige criar arquivos `.proto` (que são arquivos de definição de interface) e depois gerar código a partir deles. Para o tamanho do nosso projeto, isso seria trabalhoso demais.
- **RPyC** é direto ao ponto: você escreve a classe, coloca `exposed_` nos métodos e já está funcionando. Além disso, ele tem suporte nativo a notificações push (o servidor avisa os clientes), o que era fundamental para o nosso jogo.

A principal razão da escolha foi justamente o suporte a callbacks assíncronos. No jogo, quando um jogador envia uma dica, todos os outros precisam ser avisados imediatamente. Com RPyC e `rpyc.async_()`, o servidor consegue notificar cada cliente sem que eles precisem ficar perguntando "tem novidade?" a cada segundo (evitando polling, como o professor pediu).

---

## 2. Descrição do Desenvolvimento

### 2.1 Arquitetura Geral

A aplicação segue o modelo **cliente-servidor**: todos os clientes se conectam a um único servidor central. O servidor mantém todo o estado do jogo (jogadores, pontuação, turnos, histórico de dicas, etc.) e os clientes só precisam enviar comandos e receber notificações.

```
[Cliente A] ──────────────────────────────────────────
                                                       \
[Cliente B] ──────────────────────── [SERVIDOR]
                                                       /
[Cliente C] ──────────────────────────────────────────
```

Essa escolha foi feita porque centralizar o estado no servidor é bem mais fácil de controlar. Se os clientes se comunicassem diretamente entre si (P2P), seria muito mais difícil garantir que todos estivessem com o mesmo estado do jogo ao mesmo tempo.

### 2.2 O Servidor (`servidor.py`)

O servidor é implementado como uma classe que herda de `rpyc.Service`. Os métodos que começam com `exposed_` são os que os clientes podem chamar remotamente.

```python
import rpyc
from rpyc.utils.server import ThreadedServer

class GameServer(rpyc.Service):

    def exposed_entrar(self, nome: str) -> str:
        # o cliente chama isso para entrar no lobby
        ...

    def exposed_enviar_dica(self, dica: str) -> str:
        # o cliente chama isso para enviar uma dica no seu turno
        ...
```

O `ThreadedServer` cria uma thread separada para cada conexão de cliente, o que permite que vários jogadores se conectem ao mesmo tempo sem travar uns aos outros.

**Estado global compartilhado:**

Uma decisão importante foi guardar o estado do jogo em variáveis de classe (não de instância). No RPyC, cada conexão cria uma instância separada do `GameServer`, então se o estado ficasse em `self.variavel`, cada jogador teria seu próprio estado isolado. Como precisamos de um estado compartilhado entre todos, usamos variáveis de classe:

```python
class GameServer(rpyc.Service):
    _jogadores = {}        # todos os jogadores conectados
    _turno_atual = None    # quem está jogando agora
    _jogo_iniciado = False # se a partida já começou
    _lock = threading.Lock()
```

Para proteger essas variáveis compartilhadas de problemas de concorrência (quando duas threads tentam modificar a mesma variável ao mesmo tempo), usamos `threading.Lock()`. Sempre que modificamos o estado global, fazemos isso dentro de um `with GameServer._lock:`.

**Notificações em tempo real (callbacks):**

O ponto mais interessante da implementação foi o uso de callbacks assíncronos. Quando o servidor precisa notificar todos os jogadores (por exemplo, quando alguém envia uma dica), ele usa `rpyc.async_()` para chamar um método no lado do cliente sem bloquear:

```python
def _broadcast_sistema(self, mensagem: str):
    for nome, info in list(GameServer._jogadores.items()):
        try:
            rpyc.async_(info["conn"].root.notificar_sistema)(mensagem)
        except Exception:
            pass
```

O `rpyc.async_()` é importante aqui porque a chamada não bloqueia o servidor esperando cada cliente responder. Sem isso, se um cliente travasse, o servidor travaria também.

### 2.3 O Cliente (`cliente.py`)

O cliente também implementa um `rpyc.Service` (chamado `ClienteService`). Isso é necessário porque o servidor precisa chamar métodos no cliente para enviar notificações. Os métodos que o cliente expõe são os que o servidor pode chamar:

```python
class ClienteService(rpyc.Service):

    def exposed_notificar_sistema(self, mensagem: str):
        # servidor chama isso para mandar avisos gerais
        print(f"\n  {mensagem}")

    def exposed_receber_dica(self, remetente: str, dica: str):
        # servidor chama isso quando alguém envia uma dica
        print(f"\n  Dica de {remetente}: '{dica}'")

    def exposed_notificar_troca_solicitada(self, solicitante: str):
        # servidor avisa quando alguém quer fazer troca
        print(f"\n  {solicitante} quer fazer uma TROCA DE DICAS com você!")
```

Para que o cliente consiga receber essas notificações enquanto espera a entrada do usuário, uma thread separada fica processando os callbacks que chegam do servidor:

```python
def processar_callbacks():
    while True:
        try:
            conn.serve(0.1)  # processa callbacks por 0.1 segundos
        except EOFError:
            break

thread_callbacks = threading.Thread(target=processar_callbacks, daemon=True)
thread_callbacks.start()
```

Isso permite que o cliente esteja sempre "ouvindo" o servidor em segundo plano, sem precisar perguntar a cada instante se há novidades.

### 2.4 Funcionalidades Implementadas

#### Lobby e início da partida

Quando o primeiro jogador entra, ele se torna automaticamente o "anfitrião". Os outros jogadores que entram depois ficam no lobby aguardando. Só o anfitrião pode iniciar a partida.

Ao iniciar, o servidor sorteia um objeto secreto diferente para cada jogador (da lista com 15 objetos como espada, escudo, poção, mapa, etc.) e envia uma representação em arte ASCII de cada objeto para o respectivo dono:

```python
ARTE_OBJETOS = {
    "espada": r"""
       *
      ***
       |
   =========
       |""",
    # ... outros objetos
}
```

#### Sistema de turnos

A cada turno, o sistema avança o índice na lista de ordem de jogadores. Quando chega no fim da lista, volta para o primeiro (rodízio circular). O número máximo de turnos por rodada é calculado automaticamente: `número de jogadores × 3`.

```python
GameServer._turno_idx = (GameServer._turno_idx + 1) % len(ordem)
GameServer._turno_atual = ordem[GameServer._turno_idx]
```

Quando o limite de turnos é atingido, o jogo para automaticamente e abre uma votação.

#### Envio de dicas

Só o jogador cujo turno é o atual pode enviar dica. A dica precisa ser uma única palavra. Após enviar, o turno avança automaticamente e todos os outros jogadores são notificados via callback:

```python
def exposed_enviar_dica(self, dica: str) -> str:
    if GameServer._turno_atual != self._nome:
        return f"ERRO: não é sua vez."
    if len(dica.split()) > 1:
        return "ERRO: a dica deve ser UMA única palavra."
    # envia para todos os outros
    for nome, info in list(GameServer._jogadores.items()):
        if nome != self._nome:
            rpyc.async_(info["conn"].root.receber_dica)(self._nome, dica)
    self._avancar_turno_interno()
```

#### Troca privada de dicas

Qualquer jogador pode solicitar uma troca privada de dica com outro. O fluxo é:

1. Jogador A chama `solicitar_troca(alvo="B", minha_palavra="frio")`
2. O servidor guarda a solicitação e notifica o jogador B via callback
3. Jogador B decide aceitar ou recusar
4. Se aceitar, ambos recebem a palavra do outro
5. O servidor registra que houve uma troca (sem revelar o conteúdo) para avisar os outros jogadores

As dicas trocadas dessa forma podem ser mentira – não há verificação do conteúdo. Cada jogador pode fazer até 3 trocas por rodada.

#### Espionagem

Qualquer jogador pode registrar uma espionagem sobre a troca entre outros dois jogadores. Se a troca for realizada, o espião recebe o conteúdo da troca no início do seu próximo turno.

Se o espião for descoberto por um dos jogadores espiados, ele perde 2 pontos e quem o descobriu ganha 2 pontos:

```python
PTS_ESPIOU_PEGO = -2
```

#### Sistema de palpites

A qualquer momento (independente do turno), um jogador pode tentar adivinhar o objeto de outro. O servidor compara o palpite com o objeto real:

```python
def exposed_palpite(self, alvo: str, chute: str) -> str:
    objeto_real = GameServer._jogadores[alvo]["objeto"]
    acertou = chute.strip().lower() == objeto_real.lower()
```

#### Sistema de pontuação

| Situação | Pontos |
|---|---|
| Primeiro a adivinhar um objeto | +5 |
| Adivinhar depois de outro | +3 |
| Bônus: único a adivinhar (calculado no encerramento) | +2 |
| Dono: apenas 1 jogador adivinhou seu objeto | +2 |
| Dono: mais de 1 jogador adivinhou (redução progressiva) | +1 |
| Dono: todos os outros acertaram (dica fácil demais) | -1 |
| Ser pego espiando | -2 |
| Descobrir um espião | +2 |

A pontuação do bônus de "único acerto" é calculada no encerramento da rodada, quando o anfitrião pressiona a opção de encerrar:

```python
for dono_nome, dono_info in GameServer._jogadores.items():
    acertadores = dono_info.get("acertadores", set())
    if len(acertadores) == 1:
        unico = next(iter(acertadores))
        GameServer._jogadores[unico]["pontos"] += PTS_BONUS_UNICO_ACERTO
```

#### Chat em tempo real

O chat tem duas modalidades:

- **Público**: mensagem vai para todos os jogadores conectados via callback imediato
- **Privado**: mensagem vai apenas para o destinatário, mas o espião (se houver) também recebe

O chat é completamente separado das mecânicas do jogo. Enviar mensagem no chat não gasta turno nem interfere em nada.

#### Votação ao final da rodada

Quando o limite de turnos é atingido, uma votação é aberta automaticamente. Cada jogador vota em "continuar" ou "encerrar". Se a maioria votar em continuar, uma nova rodada começa com novos objetos para todos. Se a maioria votar em encerrar, o jogo termina e o placar final é exibido.

### 2.5 Capturas de Tela

Abaixo estão alguns exemplos de como o jogo aparece no terminal.

**Tela de conexão e lobby:**
```
====================================================
  JOGO MULTIJOGADOR DE ADIVINHAÇÃO — RPyC
====================================================
Conectando ao servidor em 127.0.0.1:18861…
Seu nome no jogo: Jefferson

  Bem-vindo ao lobby, Jefferson! Jogadores aguardando: Jefferson.
  Você é o ANFITRIÃO — use [1] para iniciar a partida.

 LOBBY — Aguardando início | Jogadores: Jefferson, Maria
────────────────────────────────────────────────────
  LOBBY — SALA DE ESPERA
  [1]  Iniciar partida (você é o anfitrião)
  [2]  Ver jogadores no lobby
  [6]  Chat Público — enviar mensagem
  [6h] Chat Público — ver histórico
  [0]  Sair
────────────────────────────────────────────────────
  Escolha:
```

**Recebendo o objeto secreto:**
```
  ⚙  [INÍCIO] A partida começou!
     Seu objeto secreto é: [ESPADA]

       *
      ***
       |
       |
       |
   =========
       |
     Guarde bem seu objeto!
```

**Menu durante a partida:**
```
  Rodada 1 | Turno 3 | Vez de: Jefferson ← SUA VEZ

────────────────────────────────────────────────────
  MENU DE AÇÕES
  ── DICAS ──────────────────────────────────────
  [1]   Enviar dica pública
  [1h]  Visualizar dicas antigas
  ── TROCAS ─────────────────────────────────────
  [2]   Solicitar troca privada de dicas
  [3]   Aceitar / Recusar troca pendente
  [4]   Espionar troca
  ── JOGO ───────────────────────────────────────
  [5]   Fazer palpite
  [P]   Passar a vez (pular turno)
  ── CHAT ───────────────────────────────────────
  [6]   Chat Público — enviar mensagem
  [6h]  Chat Público — ver histórico
  [6p]  Chat Privado — enviar mensagem
  [6ph] Chat Privado — ver histórico
  ── INFO ───────────────────────────────────────
  [7]   Ver placar detalhado
  [8]   Denunciar espião
  [9]   Listar jogadores conectados
  [10]  Relembrar meu objeto secreto
  ── RODADA ─────────────────────────────────────
  [0]   Sair
────────────────────────────────────────────────────
  Escolha:
```

**Notificação de dica em tempo real (recebida enquanto outro jogador digita):**
```
  Dica de Maria: 'afiado'
```

**Placar final:**
```
  ⚙  [FIM DE JOGO] Placar Final:
       ★ Bônus único: Jefferson foi o único a adivinhar o objeto de Maria (+2 pts)
     🥇 Jefferson: 9 pts
     🥈 Maria: 3 pts
     🥉 Carlos: 1 pts
```

---

## 3. Instruções de Instalação e Uso

### 3.1 Pré-requisitos

- Python 3.8 ou superior instalado
- Acesso a um terminal (Prompt de Comando, PowerShell ou Terminal)

### 3.2 Instalação da dependência

O único pacote necessário além do Python padrão é o RPyC. Para instalar:

```bash
pip install rpyc
```

Se quiser usar um ambiente virtual (recomendado):

```bash
# Criar o ambiente
python -m venv venv

# Ativar (Windows)
venv\Scripts\activate

# Ativar (Linux/Mac)
source venv/bin/activate

# Instalar RPyC
pip install rpyc
```

### 3.3 Iniciando o Servidor

1. Abra um terminal na pasta do projeto
2. Execute:

```bash
python servidor.py
```

A saída deve ser:
```
=======================================================
  Servidor do Jogo Multijogador — RPyC
  Porta: 18861
=======================================================
```

O servidor fica rodando em `127.0.0.1` na porta `18861`. Deixe esse terminal aberto enquanto jogar.

**Atenção:** O servidor precisa ser iniciado antes de qualquer cliente.

### 3.4 Conectando os Clientes

Para cada jogador, abra um terminal separado e execute:

```bash
python cliente.py
```

O cliente vai se conectar automaticamente ao servidor local (`127.0.0.1:18861`) e pedir um nome:

```
Seu nome no jogo: [Digite aqui seu nome]
```

São necessários pelo menos 2 jogadores para iniciar a partida.

### 3.5 Como Jogar — Passo a Passo

**1. Lobby**

- O primeiro jogador que conectar é o anfitrião
- Todos os outros jogadores entram e aguardam no lobby
- O anfitrião inicia a partida com a opção `[1]`

**2. Início da partida**

- Cada jogador recebe um objeto secreto diferente (mostrado em arte ASCII)
- O sistema define quem joga primeiro

**3. A cada turno**

- O jogador da vez deve enviar uma dica com a opção `[1]` — apenas uma palavra
- Os outros jogadores veem a dica automaticamente
- Após enviar a dica, o turno passa automaticamente para o próximo

**4. Troca privada de dicas**

- Use `[2]` para solicitar uma troca com outro jogador
- O outro jogador será notificado e deve responder com `[3]`
- Ao aceitar, cada um digita uma palavra (pode ser mentira)
- Os dois recebem a palavra do outro em privado

**5. Espionagem**

- Use `[4]` e informe os dois jogadores que você quer espionar
- No seu próximo turno, você recebe o conteúdo da troca (se ela ocorreu)
- Se você for descoberto (opção `[8]` do outro jogador), perde pontos

**6. Palpite**

- Use `[5]` a qualquer momento para tentar adivinhar o objeto de alguém
- O primeiro a acertar ganha mais pontos que os seguintes

**7. Fim da rodada**

- Quando o limite de turnos for atingido, uma votação abre automaticamente
- Cada jogador vota em `[1] Continuar` ou `[2] Encerrar`
- Se continuar: novos objetos são sorteados para todos
- Se encerrar: o placar final é exibido

**8. Chat**

- `[6]` para enviar mensagem pública para todos
- `[6p]` para enviar mensagem privada para um jogador específico
- `[6h]` e `[6ph]` para ver o histórico de mensagens

**9. Sair**

- Use `[0]` para sair do jogo

### 3.6 Rodando em Rede Local

Se quiser jogar em computadores diferentes na mesma rede, é necessário mudar o endereço IP no servidor e no cliente.

No `servidor.py`, linha 1273, troque `"127.0.0.1"` pelo IP da máquina que vai rodar o servidor:

```python
servidor = ThreadedServer(
    GameServer,
    hostname="0.0.0.0",  # aceita conexões de qualquer IP
    port=18861,
    ...
)
```

No `cliente.py`, linha 196, troque `"127.0.0.1"` pelo IP do servidor:

```python
conn = rpyc.connect(
    "192.168.X.X",  # IP do servidor na rede
    18861,
    ...
)
```

---

## 4. Considerações Finais

A principal decisão técnica do projeto foi usar RPyC com callbacks assíncronos, evitando a necessidade de polling. Isso garante que notificações como "dica recebida", "troca solicitada" e "fim de turno" cheguem imediatamente para os jogadores, sem que eles precisem ficar atualizando manualmente.

O estado centralizado no servidor facilita muito a consistência do jogo: não há como dois clientes terem versões diferentes do placar ou da ordem de turnos, porque tudo está guardado em um único lugar.

A única limitação atual é que o cliente precisa ser executado no terminal, sem interface gráfica. Para um trabalho futuro, seria interessante criar uma interface com Tkinter ou PyQt mantendo a mesma lógica de comunicação RPyC que já existe.
