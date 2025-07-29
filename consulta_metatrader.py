import MetaTrader5 as mt5
import os
from datetime import datetime, timedelta
import re
from dotenv import load_dotenv

def get_base_asset(asset_code):
    """Extrai a parte base do código do ativo (ex: PETR4 -> PETR, SANB11 -> SANB)."""
    match = re.match(r"([A-Z]+)", asset_code.upper())
    if match:
        return match.group(1)
    return asset_code.upper() # Fallback

def inicializar_mt5(login, password, server):
    """Inicializa a conexão com o MetaTrader 5."""
    print("Tentando inicializar MetaTrader 5...")
    if not mt5.initialize():
        print(f"Erro ao inicializar MetaTrader 5: {mt5.last_error()}")
        return False
    print("MetaTrader 5 inicializado com sucesso!")
    return True

def obter_opcoes_mt5(base_ativo_config):
    """Obtém informações brutas de opções para o ativo base especificado."""
    raw_opcoes_info = []
    symbols = mt5.symbols_get()
    if not symbols:
        print("Nenhum símbolo retornado por mt5.symbols_get().")
        return []

    print(f"Verificando símbolos para o ativo base: {base_ativo_config}...")
    for s in symbols:
        if not (hasattr(s, "path") and "BOVESPA" in s.path.upper()):
            continue

        # Verifica se o nome do símbolo começa com o código base do ativo
        if not s.name.upper().startswith(base_ativo_config):
            continue

        info = mt5.symbol_info(s.name)
        if not info:
            continue

        # Verifica se é uma opção (Europeia ou Americana)
        if not (info.option_mode == mt5.SYMBOL_OPTION_MODE_EUROPEAN or
                info.option_mode == mt5.SYMBOL_OPTION_MODE_AMERICAN):
            continue

        # Adiciona o objeto SymbolInfo completo para processamento posterior
        raw_opcoes_info.append(info)

    print(f"Encontradas {len(raw_opcoes_info)} opções brutas candidatas para o ativo base {base_ativo_config}.")
    return raw_opcoes_info

def filtrar_opcoes_mt5(opcoes_info_list, config):
    """Filtra e processa as opções com base nos critérios definidos."""
    opcoes_filtradas_list = []
    vencimento_maximo_dt = config["vencimento_maximo"]

    # Loop de DEBUG atualizado para usar symbol_info_tick
    # Este loop é para depuração e pode ser removido ou comentado posteriormente.
    for info_debug_loop in opcoes_info_list:
        if info_debug_loop.name.upper() == "PETRF331": # Alvo do debug
            venc_debug = datetime.fromtimestamp(info_debug_loop.expiration_time).strftime("%Y-%m-%d") if info_debug_loop.expiration_time else "N/A"
            tipo_val_debug = getattr(info_debug_loop, "option_right", "N/A")
            tipo_str_debug = "CALL" if tipo_val_debug == 0 else "PUT" if tipo_val_debug == 1 else "N/A"
            
            debug_tick = mt5.symbol_info_tick(info_debug_loop.name)
            debug_ultimo_preco = debug_tick.last if debug_tick else 0.0
            debug_preco_venda = debug_tick.ask if debug_tick else 0.0
            
            print(f"[DEBUG] {info_debug_loop.name} | Venc: {venc_debug} | Tipo: {tipo_str_debug} ({tipo_val_debug}) | "
                  f"Último Preço (de tick): {debug_ultimo_preco} | ASK (de tick): {debug_preco_venda}")
            # Considere adicionar um 'break' se quiser debugar apenas a primeira ocorrência
            # break 
        
    # Loop de processamento principal
    for info in opcoes_info_list:
        nome_opcao = info.name

        # Determinar tipo da opção (CALL ou PUT)
        if hasattr(info, "option_right"):
            if info.option_right == 0:
                tipo_opcao = "CALL"
            elif info.option_right == 1:
                tipo_opcao = "PUT"
            else:
                continue
        else:
            continue

        if tipo_opcao.upper() != config["tipo_opcao_filtro"].upper():
            continue

        # Data de Vencimento
        if info.expiration_time == 0:
            continue
        vencimento_opcao_dt = datetime.fromtimestamp(info.expiration_time)
        if vencimento_opcao_dt > vencimento_maximo_dt:
            continue

        # Obter dados de tick e informações do ativo
        # É importante garantir que o símbolo esteja selecionado no MarketWatch do MT5
        # para que symbol_info_tick funcione corretamente.
        # Você pode adicionar: mt5.symbol_select(nome_opcao, True)
        # No entanto, isso pode adicionar sobrecarga. Teste primeiro sem.
        mt5.symbol_select(nome_opcao, True
        tick = mt5.symbol_info_tick(nome_opcao)
        if not tick:
            # print(f"Skipping {nome_opcao}: No tick data.") # Log opcional
            continue

        # MODIFICAÇÃO PRINCIPAL: Usar tick.last para ultimo_preco e tick.ask para preco_venda
        ultimo_preco = tick.last
        preco_venda = tick.ask
        
        # Timestamp do último negócio (do objeto info)
        last_trade_actual_time_ts = info.lasttime 

        # Filtro de preços nulos ou zerados e timestamp do último negócio
        if ultimo_preco is None or preco_venda is None or \
           ultimo_preco == 0.0 or preco_venda == 0.0 or \
           last_trade_actual_time_ts == 0:
            # print(f"Skipping {nome_opcao}: Preços zerados/nulos ou last_trade_time zerado. "
            #       f"Last: {ultimo_preco}, Ask: {preco_venda}, LastTimeTS: {last_trade_actual_time_ts}") # Log opcional
            continue

        # Obter quantidade disponível no book de ofertas no melhor preço de venda
        quantidade_disponivel_ask = 0.0
        if preco_venda > 0:
            book = mt5.market_book_get(nome_opcao)
            if book:
                for item in book:
                    if item.type == mt5.ORDER_TYPE_SELL and abs(item.price - preco_venda) < 1e-5:
                        quantidade_disponivel_ask += item.volume

        if quantidade_disponivel_ask == 0.0:
            continue
            
        try:
            ultimo_preco_float = float(ultimo_preco)
            preco_venda_float = float(preco_venda)
            quantidade_float = float(quantidade_disponivel_ask)
            last_trade_datetime = datetime.fromtimestamp(last_trade_actual_time_ts)
        except (ValueError, TypeError, OSError):
            continue
        
        # Filtro: negociada até X dias atrás (baseado no último negócio real)
        if last_trade_datetime < datetime.now() - timedelta(days=config["days_ignorar"]):
            continue

        limite_superior = ultimo_preco_float * (1 + config["percentual"] / 100)
        valor_total_negociavel = preco_venda_float * quantidade_float
        pct_venda_ultimo = ((preco_venda_float / ultimo_preco_float) - 1) * 100 if ultimo_preco_float > 0 else 0

        if preco_venda_float <= limite_superior and valor_total_negociavel >= config["valor_minimo_negociavel"]:
            opcoes_filtradas_list.append({
                "nome": nome_opcao,
                "tipo": tipo_opcao,
                "vencimento": vencimento_opcao_dt.strftime("%Y-%m-%d"),
                "ultimo_preco": ultimo_preco_float,
                "preco_venda": preco_venda_float,
                "quantidade_disponivel": quantidade_float,
                "valor_total_negociavel": valor_total_negociavel,
                "percentual_venda_sobre_ultimo": pct_venda_ultimo,
                "ultima_negociacao": last_trade_datetime.strftime("%Y-%m-%d %H:%M:%S")
            })
            
    return opcoes_filtradas_list

def exibir_opcoes_mt5(opcoes, config_display):
    """Exibe as opções filtradas no formato desejado."""
    ativo = config_display["ativo"]
    percentual = config_display["percentual"]
    valor_minimo = config_display["valor_minimo_negociavel"]

    if not opcoes:
        print(f"Não foram encontradas opções de {ativo} que atendam aos critérios especificados (incluindo valor total negociável mínimo de R$ {valor_minimo:.2f}).")
        return

    opcoes.sort(key=lambda x: x["valor_total_negociavel"], reverse=True)
    print(f"\nOpções de {ativo} com preço de venda até {percentual}% acima do último preço negociado, preços não nulos e valor total negociável mínimo de R$ {valor_minimo:.2f}:")
    for o in opcoes:
        print(
            f"{o['nome']} - {o['tipo']}- {o['vencimento']} - "
            f"LAST:{o['ultimo_preco']:.2f} -  ASK:{o['preco_venda']:.2f}-"
            f" PCT:{o['percentual_venda_sobre_ultimo']:.2f}% - "
            f"DTHLAST: {o['ultima_negociacao']} -  "
            f"QuantD.{o['quantidade_disponivel']:.0f}, "
            f"VTD: R$ {o['valor_total_negociavel']:.2f}"
        )

def main():
    load_dotenv() # Carrega variáveis de ambiente do arquivo .env

    # Configurações
    config = {
        "ativo": "PETR4",  # Código do ativo (ex: CYRE3, PETR4, SANB11)
        "percentual": 30,  # Porcentagem sobre o último preço negociado da opção
        "valor_minimo_negociavel": 100,  # Valor mínimo total negociável em reais
        "tipo_opcao_filtro": "CALL",  # "CALL" ou "PUT"
        "days_ignorar": 2,  # Dias para ignorar opções não negociadas recentemente
        "vencimento_maximo_str": "2025-11-20", # Formato YYYY-MM-DD
        "mt5_login": int(os.getenv("MT5_LOGIN", 0)),
        "mt5_password": os.getenv("MT5_PASSWORD", ""),
        "mt5_server": os.getenv("MT5_SERVER", "")
    }
    try:
        config["vencimento_maximo"] = datetime.strptime(config["vencimento_maximo_str"], "%Y-%m-%d")
    except ValueError:
        print(f"Formato de data inválido para vencimento_maximo_str: {config['vencimento_maximo_str']}")
        return
        
    config["base_ativo"] = get_base_asset(config["ativo"])

    if not inicializar_mt5(config["mt5_login"], config["mt5_password"], config["mt5_server"]):
        return

    try:
        raw_opcoes_info = obter_opcoes_mt5(config["base_ativo"])
        print(f"Primeiras opções extraídas (brutas):")
        # for info in raw_opcoes_info[:20]:  # Mostra só as 20 primeiras
        #     venc = datetime.fromtimestamp(info.expiration_time).strftime("%Y-%m-%d") if info.expiration_time else "N/A"
        #     print(f"{info.name} | Venc: {venc} | Tipo: {getattr(info, 'option_right', 'N/A')}")        
        if not raw_opcoes_info:
            print(f"Nenhuma opção bruta encontrada para o ativo base {config['base_ativo']} após a filtragem inicial.")
        else:
            opcoes_processadas = filtrar_opcoes_mt5(raw_opcoes_info, config)
            exibir_opcoes_mt5(opcoes_processadas, config)

    except Exception as e:
        print(f"Ocorreu um erro durante a execução principal: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("Desligando MetaTrader 5...")
        mt5.shutdown()

if __name__ == "__main__":
    main()
