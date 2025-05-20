import telebot
from telebot import types
import sqlite3
from datetime import datetime, timedelta
import pandas as pd
import io

# --- Configurações do Bot ---
TOKEN = '7815937334:AAH13dW8xroTYLmprmLFSgULCCrteSaYrr8' # SUBSTITUA PELO SEU TOKEN REAL DO BOTFATHER
bot = telebot.TeleBot(TOKEN)

# --- Configurações do Banco de Dados ---
DB_NAME = 'financas.db'

# --- Estados para controlar o fluxo de entrada de dados ---
estados = {}
ESPERANDO_TIPO = 1
ESPERANDO_VALOR = 2
ESPERANDO_DESCRICAO = 3
ESPERANDO_VALOR_RESGATE = 4
ESPERANDO_CATEGORIA = 5

# Dicionário temporário para guardar dados da transação atual antes de salvar no DB
transacao_atual = {} # {user_id: {'tipo': 'entrada', 'valor': 100.0, 'categoria': 'Alimentação'}}

# Categorias padrão (pode ser expandido ou gerenciado por DB no futuro)
CATEGORIAS_PADRAO = ['Alimentação', 'Transporte', 'Moradia', 'Lazer', 'Educação', 'Saúde', 'Salário', 'Investimento', 'Outros']

# --- Funções do Banco de Dados ---
def connect_db():
    """Conecta ao banco de dados SQLite."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row # Permite acessar colunas por nome
    return conn

def create_table():
    """Cria a tabela de transações se ela não existir e adiciona colunas se necessário."""
    conn = connect_db()
    cursor = conn.cursor()

    # Cria a tabela 'transacoes'
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transacoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            tipo TEXT NOT NULL,
            valor REAL NOT NULL,
            descricao TEXT,
            data TEXT NOT NULL
        )
    ''')
    conn.commit() # Confirma a criação da tabela principal

    # Adiciona a coluna 'categoria' se não existir
    try:
        cursor.execute("ALTER TABLE transacoes ADD COLUMN categoria TEXT")
    except sqlite3.OperationalError as e:
        if "duplicate column name: categoria" not in str(e):
            print(f"Erro ao adicionar coluna 'categoria': {e}")
        pass # Coluna já existe

    # Adiciona a coluna 'conta_id' se não existir (para futuras melhorias de múltiplas contas)
    try:
        cursor.execute("ALTER TABLE transacoes ADD COLUMN conta_id INTEGER DEFAULT 1")
    except sqlite3.OperationalError as e:
        if "duplicate column name: conta_id" not in str(e):
            print(f"Erro ao adicionar coluna 'conta_id': {e}")
        pass # Coluna já existe
    
    conn.commit() # Confirma as alterações para as colunas
    conn.close() # Fecha a conexão APENAS AQUI, no final da função

# A tabela de metas e funções relacionadas foram removidas aqui.

def insert_transaction(user_id, tipo, valor, descricao, categoria, conta_id=1):
    """Insere uma nova transação no banco de dados."""
    conn = connect_db()
    cursor = conn.cursor()
    data_hora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        "INSERT INTO transacoes (user_id, tipo, valor, descricao, data, categoria, conta_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, tipo, valor, descricao, data_hora, categoria, conta_id)
    )
    conn.commit()
    conn.close()

def get_user_transactions(user_id, start_date=None, end_date=None):
    """
    Retorna transações de um usuário, opcionalmente filtradas por um período de data.
    Inclui a categoria.
    """
    conn = connect_db()
    cursor = conn.cursor()
    
    query = "SELECT tipo, valor, descricao, data, categoria FROM transacoes WHERE user_id = ?"
    params = [user_id] # Use uma lista para params para que possamos adicionar mais itens

    if start_date and end_date:
        query += " AND data BETWEEN ? AND ?"
        params.extend([start_date, end_date])
    elif start_date:
        query += " AND data >= ?"
        params.append(start_date)
    elif end_date:
        query += " AND data <= ?"
        params.append(end_date)

    cursor.execute(query + " ORDER BY data DESC", params)
    
    rows = cursor.fetchall()
    conn.close()
    
    transactions = []
    for row in rows:
        transactions.append({
            'tipo': row['tipo'],
            'valor': row['valor'],
            'descricao': row['descricao'],
            'data': row['data'],
            'categoria': row['categoria']
        })
    return transactions

def get_total_poupado(user_id):
    """Calcula o valor total poupado/investido por um usuário em todas as transações."""
    conn = connect_db()
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT SUM(CASE WHEN tipo = 'poupanca' THEN valor ELSE 0 END) - SUM(CASE WHEN tipo = 'resgate_poupanca' THEN valor ELSE 0 END) FROM transacoes WHERE user_id = ?",
        (user_id,)
    )
    total_poupado = cursor.fetchone()[0]
    conn.close()
    return total_poupado if total_poupado is not None else 0.0

def calculate_current_balance(user_id, up_to_date=None):
    """
    Calcula o saldo geral do usuário até uma data específica (inclusive).
    Poupança é considerada uma saída do saldo geral.
    Resgate de poupança é considerado uma entrada no saldo geral.
    """
    conn = connect_db()
    cursor = conn.cursor()
    
    query = "SELECT tipo, valor FROM transacoes WHERE user_id = ?"
    params = [user_id]

    if up_to_date:
        query += " AND data <= ?"
        params.append(up_to_date.strftime("%Y-%m-%d %H:%M:%S"))
    
    cursor.execute(query, params)
    transactions = cursor.fetchall()
    conn.close()

    total_entradas = sum(t['valor'] for t in transactions if t['tipo'] in ['entrada', 'resgate_poupanca'])
    total_saidas = sum(t['valor'] for t in transactions if t['tipo'] in ['saida', 'poupanca'])
    
    return total_entradas - total_saidas

# --- Funções do Bot ---
def menu_inicial(chat_id, message_text="O que mais você gostaria de fazer?"):
    """Envia o menu inicial com os botões."""
    markup = types.InlineKeyboardMarkup()
    btn_adicionar = types.InlineKeyboardButton("Adicionar Transação", callback_data='adicionar')
    btn_sacar_poupanca = types.InlineKeyboardButton("Sacar da Poupança", callback_data='sacar_poupanca')
    btn_relatorio = types.InlineKeyboardButton("Gerar Relatório Mensal", callback_data='relatorio_mensal_init')
    btn_investimentos = types.InlineKeyboardButton("Relatório de Investimentos", callback_data='relatorio_investimentos')
    # btn_metas = types.InlineKeyboardButton("Minhas Metas Financeiras", callback_data='gerenciar_metas') # Removido
    btn_exportar = types.InlineKeyboardButton("Exportar Dados (CSV)", callback_data='exportar_dados')
    markup.add(btn_adicionar)
    markup.add(btn_sacar_poupanca)
    markup.add(btn_relatorio, btn_investimentos)
    # markup.add(btn_metas) # Removido
    markup.add(btn_exportar)
    bot.send_message(chat_id, message_text, reply_markup=markup)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    """Envia uma mensagem de boas-vindas e o menu inicial."""
    user_id = message.from_user.id
    bot.reply_to(message, f"Olá {message.from_user.first_name}! Sou seu assistente financeiro. Como posso te ajudar hoje?")
    estados[user_id] = None # Resetando o estado ao iniciar
    transacao_atual.pop(user_id, None) # Limpa dados de transação anteriores, se houver
    menu_inicial(message.chat.id) # Chama a função para exibir o menu inicial

@bot.callback_query_handler(func=lambda call: True)
def callback_inline(call):
    """Processa as interações dos botões inline."""
    try:
        user_id = call.from_user.id
        chat_id = call.message.chat.id
        message_id = call.message.message_id

        if call.data == 'adicionar':
            markup = types.InlineKeyboardMarkup()
            btn_entrada = types.InlineKeyboardButton("Entrada", callback_data='entrada')
            btn_saida = types.InlineKeyboardButton("Saída", callback_data='saida')
            btn_poupanca = types.InlineKeyboardButton("Poupança/Investimento", callback_data='poupanca')
            markup.add(btn_entrada, btn_saida, btn_poupanca)
            try:
                bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="Qual o tipo da transação?", reply_markup=markup)
            except telebot.apihelper.ApiTelegramException as e:
                print(f"Erro ao editar mensagem: {e}") 
                bot.send_message(chat_id=chat_id, text="Qual o tipo da transação?", reply_markup=markup)
            estados[user_id] = ESPERANDO_TIPO
            transacao_atual[user_id] = {}
        elif call.data == 'entrada':
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="Qual o valor da entrada?")
            estados[user_id] = ESPERANDO_VALOR
            transacao_atual[user_id]['tipo'] = 'entrada'
        elif call.data == 'saida':
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="Qual o valor da saída?")
            estados[user_id] = ESPERANDO_VALOR
            transacao_atual[user_id]['tipo'] = 'saida'
        elif call.data == 'poupanca':
            bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="Qual o valor que você poupou/investiu?")
            estados[user_id] = ESPERANDO_VALOR
            transacao_atual[user_id]['tipo'] = 'poupanca'
        
        # --- Sacar da Poupança ---
        elif call.data == 'sacar_poupanca':
            total_poupado = get_total_poupado(user_id)
            if total_poupado <= 0:
                bot.edit_message_text(chat_id=chat_id, message_id=message_id, 
                                      text=f"Você não possui saldo na poupança para sacar (R$ {total_poupado:.2f}).")
                menu_inicial(chat_id)
            else:
                bot.edit_message_text(chat_id=chat_id, message_id=message_id, 
                                      text=f"Quanto você gostaria de sacar da poupança? (Saldo atual: R$ {total_poupado:.2f})")
                estados[user_id] = ESPERANDO_VALOR_RESGATE
                transacao_atual[user_id] = {'tipo': 'resgate_poupanca', 'saldo_poupanca_disponivel': total_poupado}

        # --- Relatórios ---
        elif call.data == 'relatorio_mensal_init':
            today = datetime.now()
            gerar_relatorio_mensal(call, user_id, today.month, today.year)
        
        elif call.data.startswith('relatorio_mes_'):
            parts = call.data.split('_')
            current_month = int(parts[3])
            current_year = int(parts[4])
            
            current_date_obj = datetime(current_year, current_month, 1)
            direction = parts[2] 
            if direction == 'prev':
                new_date = current_date_obj - timedelta(days=1)
            elif direction == 'next':
                new_date = current_date_obj + timedelta(days=32) 
            
            gerar_relatorio_mensal(call, user_id, new_date.month, new_date.year)

        elif call.data == 'relatorio_investimentos':
            gerar_relatorio_investimentos(call, user_id)
        
        # --- Exportar Dados ---
        elif call.data == 'exportar_dados':
            export_transactions_to_csv(chat_id, user_id)
        
        # --- Voltar ao Menu Principal ---
        elif call.data == 'voltar_menu':
            voltar_menu_handler(call)

    except Exception as e:
        print(f"Erro no callback: {e}")
        bot.send_message(chat_id, "Ocorreu um erro. Por favor, tente novamente.")
        estados[user_id] = None
        transacao_atual.pop(user_id, None)
        menu_inicial(chat_id)


@bot.message_handler(func=lambda message: get_estado(message.from_user.id) == ESPERANDO_VALOR)
def receber_valor(message):
    """Recebe o valor da transação (entrada, saída, poupança)."""
    user_id = message.from_user.id
    try:
        valor = float(message.text.replace(',', '.')) # Permite vírgula como separador decimal
        if valor <= 0:
            bot.reply_to(message, "O valor deve ser positivo. Por favor, digite um valor válido.")
            return

        transacao_atual[user_id]['valor'] = valor
        
        # Pergunta a categoria após o valor
        markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
        for cat in CATEGORIAS_PADRAO:
            markup.add(cat)
        markup.add("Outra Categoria") # Adiciona um botão para "Outra Categoria"

        bot.reply_to(message, "Agora, qual a categoria desta transação? Você pode escolher uma das opções ou digitar uma nova.", reply_markup=markup)
        estados[user_id] = ESPERANDO_CATEGORIA

    except ValueError:
        bot.reply_to(message, "Por favor, digite um valor numérico válido.")

@bot.message_handler(func=lambda message: get_estado(message.from_user.id) == ESPERANDO_CATEGORIA)
def receber_categoria(message):
    """Recebe a categoria da transação."""
    user_id = message.from_user.id
    categoria = message.text.strip() # Remove espaços em branco
    
    if not categoria:
        bot.reply_to(message, "A categoria não pode ser vazia. Por favor, digite uma categoria.")
        return

    transacao_atual[user_id]['categoria'] = categoria
    
    # Remove o teclado de resposta personalizada
    markup_remove = types.ReplyKeyboardRemove(selective=False)

    if transacao_atual[user_id].get('tipo') == 'poupanca':
        bot.reply_to(message, "Qual a descrição para esta poupança/investimento? (ex: CDB, Ações, Fundo de Emergência)", reply_markup=markup_remove)
    else:
        bot.reply_to(message, "Qual a descrição desta transação?", reply_markup=markup_remove)
    estados[user_id] = ESPERANDO_DESCRICAO

@bot.message_handler(func=lambda message: get_estado(message.from_user.id) == ESPERANDO_VALOR_RESGATE)
def receber_valor_resgate(message):
    """Recebe o valor para saque da poupança e processa."""
    user_id = message.from_user.id
    try:
        valor_resgate = float(message.text.replace(',', '.')) # Permite vírgula
        saldo_disponivel = transacao_atual[user_id].get('saldo_poupanca_disponivel', 0.0)

        if valor_resgate <= 0:
            bot.reply_to(message, "O valor do saque deve ser positivo. Por favor, digite um valor válido.")
            return
        
        if valor_resgate > saldo_disponivel:
            bot.reply_to(message, f"Valor de saque maior que o saldo disponível na poupança (R$ {saldo_disponivel:.2f}). Por favor, digite um valor válido.")
            return

        insert_transaction(user_id, 'resgate_poupanca', valor_resgate, "Saque da poupança", "Resgate") 
        bot.reply_to(message, f"R$ {valor_resgate:.2f} sacados da poupança com sucesso! Este valor já está disponível no seu saldo geral.")
        
        estados[user_id] = None
        transacao_atual.pop(user_id, None)
        menu_inicial(user_id)

    except ValueError:
        bot.reply_to(message, "Por favor, digite um valor numérico válido para o saque.")
        
    except Exception as e:
        print(f"Erro ao processar saque da poupança: {e}")
        bot.reply_to(message, "Ocorreu um erro ao processar o saque. Por favor, tente novamente.")
        estados[user_id] = None
        transacao_atual.pop(user_id, None)
        menu_inicial(user_id)


@bot.message_handler(func=lambda message: get_estado(message.from_user.id) == ESPERANDO_DESCRICAO)
def receber_descricao(message):
    """Recebe a descrição da transação, salva no DB e retorna ao menu."""
    user_id = message.from_user.id
    descricao = message.text # A descrição é pega exatamente como digitada

    if user_id in transacao_atual and 'tipo' in transacao_atual[user_id] and 'valor' in transacao_atual[user_id] and 'categoria' in transacao_atual[user_id]:
        tipo = transacao_atual[user_id]['tipo']
        valor = transacao_atual[user_id]['valor']
        categoria = transacao_atual[user_id]['categoria']

        insert_transaction(user_id, tipo, valor, descricao, categoria)

        bot.reply_to(message, f"Transação de *{tipo}* no valor de R$ {valor:.2f} ({descricao}) em *{categoria}* adicionada com sucesso!", parse_mode='Markdown')
        menu_inicial(user_id, "Transação salva! Você pode adicionar outra, gerar um relatório ou escolher outra opção.")
    else:
        bot.reply_to(message, "Ocorreu um erro ao processar a descrição. A transação não foi salva. Por favor, tente novamente.")
        menu_inicial(user_id, "Ocorreu um problema. Por favor, tente novamente.")

    estados[user_id] = None # Resetando o estado
    transacao_atual.pop(user_id, None) # Limpa os dados temporários da transação

# --- Funções de Relatórios Avançados (Com Categorias) ---
def gerar_relatorio_mensal(call, user_id, month, year):
    """Gera um relatório mensal das transações a partir dos dados do DB, incluindo resumo por categoria."""
    start_of_month = datetime(year, month, 1)
    if month == 12:
        end_of_month = datetime(year, 12, 31, 23, 59, 59)
    else:
        end_of_month = datetime(year, month + 1, 1) - timedelta(microseconds=1)
    
    dados_do_mes = get_user_transactions(user_id, start_of_month.strftime("%Y-%m-%d %H:%M:%S"), end_of_month.strftime("%Y-%m-%d %H:%M:%S"))
    
    saldo_atual_geral = calculate_current_balance(user_id, up_to_date=end_of_month)
    
    nomes_meses = {
        1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril", 5: "Maio", 6: "Junho",
        7: "Julho", 8: "Agosto", 9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro"
    }
    mes_atual_nome = nomes_meses.get(month, "Mês Desconhecido")

    entradas_mes = sum(item['valor'] for item in dados_do_mes if item['tipo'] == 'entrada')
    saidas_gerais_mes = sum(item['valor'] for item in dados_do_mes if item['tipo'] == 'saida')
    poupado_mes = sum(item['valor'] for item in dados_do_mes if item['tipo'] == 'poupanca')
    resgate_mes = sum(item['valor'] for item in dados_do_mes if item['tipo'] == 'resgate_poupanca')

    saldo_liquido_mes = entradas_mes + resgate_mes - saidas_gerais_mes - poupado_mes

    relatorio_text = f"*📊 Relatório Financeiro - {mes_atual_nome}/{year} 📊*\n\n"
    relatorio_text += f"*💰 Saldo Total Acumulado (até {mes_atual_nome}/{year}):* R$ {saldo_atual_geral:.2f}\n"
    relatorio_text += f"------------------------------------\n"
    relatorio_text += f"*Entradas no Mês:* R$ {entradas_mes:.2f}\n"
    relatorio_text += f"*Saídas Gerais no Mês:* R$ {saidas_gerais_mes:.2f}\n"
    relatorio_text += f"*Valor Poupado/Investido no Mês:* R$ {poupado_mes:.2f}\n"
    relatorio_text += f"*Valor Resgatado da Poupança no Mês:* R$ {resgate_mes:.2f}\n"
    relatorio_text += f"*Resultado Líquido do Mês:* R$ {saldo_liquido_mes:.2f}\n\n"
    
    if not dados_do_mes:
        relatorio_text += "Nenhuma transação registrada para este mês."
    else:
        # Resumo por categoria
        saidas_por_categoria = {}
        for item in dados_do_mes:
            if item['tipo'] == 'saida':
                categoria = item['categoria'] if item['categoria'] else 'Sem Categoria'
                saidas_por_categoria[categoria] = saidas_por_categoria.get(categoria, 0.0) + item['valor']
        
        if saidas_por_categoria:
            relatorio_text += "*Resumo de Gastos por Categoria:*\n"
            sorted_categories = sorted(saidas_por_categoria.items(), key=lambda item: item[1], reverse=True)
            for categoria, valor in sorted_categories:
                relatorio_text += f"- {categoria}: R$ {valor:.2f}\n"
            relatorio_text += "\n"

        relatorio_text += "*Detalhes das Transações do Mês:*\n"
        sorted_transactions = sorted(dados_do_mes, key=lambda x: datetime.strptime(x['data'], "%Y-%m-%d %H:%M:%S"), reverse=True)
        
        for transacao in sorted_transactions:
            data_obj = datetime.strptime(transacao['data'], "%Y-%m-%d %H:%M:%S")
            data_formatada = data_obj.strftime("%d/%m/%Y")
            
            tipo_exibicao = transacao['tipo'].capitalize()
            if transacao['tipo'] == 'poupanca':
                tipo_exibicao = "Poupança/Inv."
            elif transacao['tipo'] == 'resgate_poupanca':
                tipo_exibicao = "Resgate Poupança"
            
            categoria_exibicao = f" ({transacao['categoria']})" if transacao['categoria'] else ""
            relatorio_text += f"- {data_formatada} | {tipo_exibicao}: R$ {transacao['valor']:.2f}{categoria_exibicao} ({transacao['descricao']})\n"

    markup = types.InlineKeyboardMarkup()
    btn_prev_month = types.InlineKeyboardButton("⬅️ Mês Anterior", callback_data=f'relatorio_mes_prev_{month}_{year}')
    btn_next_month = types.InlineKeyboardButton("Mês Seguinte ➡️", callback_data=f'relatorio_mes_next_{month}_{year}')
    markup.add(btn_prev_month, btn_next_month)
    markup.add(types.InlineKeyboardButton("Voltar ao Menu Principal", callback_data='voltar_menu'))

    try:
        bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, 
                              text=relatorio_text, reply_markup=markup, parse_mode='Markdown')
    except telebot.apihelper.ApiTelegramException as e:
        print(f"Erro ao editar mensagem do relatório mensal: {e}")
        bot.send_message(chat_id=call.message.chat.id, text=relatorio_text, reply_markup=markup, parse_mode='Markdown')


def gerar_relatorio_investimentos(call, user_id):
    """Gera um relatório detalhado de todas as transações de poupança/investimento."""
    conn = connect_db()
    cursor = conn.cursor()
    cursor.execute("SELECT tipo, valor, descricao, data, categoria FROM transacoes WHERE user_id = ? AND (tipo = 'poupanca' OR tipo = 'resgate_poupanca') ORDER BY data DESC", (user_id,))
    investments_and_resgates = cursor.fetchall()
    conn.close()

    total_investido = get_total_poupado(user_id)

    relatorio_inv_text = "*📈 Relatório de Investimentos 📈*\n\n"
    relatorio_inv_text += f"*Total Acumulado em Poupança/Investimentos:* R$ {total_investido:.2f}\n\n"
    relatorio_inv_text += "*Detalhes dos Lançamentos:*\n"

    if not investments_and_resgates:
        relatorio_inv_text += "Nenhum investimento ou resgate registrado ainda."
    else:
        for item in investments_and_resgates:
            data_obj = datetime.strptime(item['data'], "%Y-%m-%d %H:%M:%S")
            data_formatada = data_obj.strftime("%d/%m/%Y")
            
            categoria_exibicao = f" ({item['categoria']})" if item['categoria'] else ""
            
            if item['tipo'] == 'poupanca':
                tipo_exibicao = "Poupança/Inv."
                valor_exibicao = f"R$ {item['valor']:.2f} (Adicionado)"
            elif item['tipo'] == 'resgate_poupanca':
                tipo_exibicao = "Resgate Poupança"
                valor_exibicao = f"R$ {item['valor']:.2f} (Resgatado)"
            
            relatorio_inv_text += f"- {data_formatada} | {tipo_exibicao}: {valor_exibicao}{categoria_exibicao} ({item['descricao']})\n"

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("Voltar ao Menu Principal", callback_data='voltar_menu'))

    try:
        bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id,
                              text=relatorio_inv_text, reply_markup=markup, parse_mode='Markdown')
    except telebot.apihelper.ApiTelegramException as e:
        print(f"Erro ao editar mensagem do relatório de investimentos: {e}")
        bot.send_message(chat_id=call.message.chat.id, text=relatorio_inv_text, reply_markup=markup, parse_mode='Markdown')


# --- Exportar Dados ---
def export_transactions_to_csv(chat_id, user_id):
    transactions = get_user_transactions(user_id) # Pega todas as transações
    if not transactions:
        bot.send_message(chat_id, "Você não possui transações para exportar.")
        menu_inicial(chat_id)
        return

    # Processa os dados para o formato desejado antes de criar o DataFrame
    processed_transactions = []
    for t in transactions:
        # Formata a data para DD/MM/AAAA
        data_obj = datetime.strptime(t['data'], "%Y-%m-%d %H:%M:%S")
        data_formatada = data_obj.strftime("%d/%m/%Y %H:%M:%S") # Inclui hora, minuto e segundo

        # Garante que a descrição seja exatamente como digitada
        descricao_limpa = t['descricao']

        processed_transactions.append({
            'Data': data_formatada,
            'Tipo': t['tipo'].capitalize(),
            'Valor': f"{t['valor']:.2f}".replace('.', ','), # Formata valor para 2 casas decimais e usa vírgula
            'Categoria': t['categoria'],
            'Descricao': descricao_limpa
        })

    # Converte a lista de dicionários para um DataFrame do pandas
    df = pd.DataFrame(processed_transactions)
    
    # Cria um buffer para armazenar o CSV em memória
    output = io.StringIO()
    # Usando 'sep=';'' para compatibilidade com Excel no Brasil e 'decimal=','' para valores
    df.to_csv(output, index=False, encoding='utf-8', sep=';') 
    output.seek(0) # Volta ao início do buffer
    
    # Envia o arquivo CSV
    bot.send_document(chat_id, ('transacoes.csv', output.getvalue()))
    bot.send_message(chat_id, "Seu relatório de transações foi exportado para CSV.")
    menu_inicial(chat_id)


def get_estado(user_id):
    """Retorna o estado atual do usuário."""
    return estados.get(user_id)

# --- Handler para mensagens não filtradas (Sempre no final) ---
@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    """Lida com mensagens não filtradas e retorna ao menu principal."""
    user_id = message.from_user.id
    bot.reply_to(message, "Desculpe, não entendi o que você digitou. Por favor, utilize os botões ou os comandos.")
    estados[user_id] = None
    transacao_atual.pop(user_id, None)
    menu_inicial(user_id)

# --- Handler para Voltar ao Menu Principal ---
@bot.callback_query_handler(func=lambda call: call.data == 'voltar_menu')
def voltar_menu_handler(call):
    chat_id = call.message.chat.id
    
    try:
        # Tenta deletar a mensagem original do callback, se ainda existir
        bot.delete_message(chat_id=chat_id, message_id=call.message.message_id)
    except telebot.apihelper.ApiTelegramException as e:
        print(f"Erro ao deletar mensagem (pode ser normal se a mensagem já foi editada): {e}")
        pass # Ignora o erro se a mensagem já foi apagada ou alterada
        
    estados[call.from_user.id] = None
    transacao_atual.pop(call.from_user.id, None)
    menu_inicial(chat_id, "Voltando ao menu principal.") 
    
# --- Inicialização ---
if __name__ == '__main__':
    create_table() # Garante que a tabela de transações e suas colunas existam
    # create_goals_table() # A chamada para criar a tabela de metas foi removida
    print("Bot rodando...")
    bot.polling(none_stop=True)