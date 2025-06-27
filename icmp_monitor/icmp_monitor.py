#!/usr/bin/env -S uv run python
# -*- coding: utf-8 -*-
"""ICMP Monitor - Advanced ICMP monitoring tool with statistical analysis"""

import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime

import psutil
import requests
from rich import box
from rich.align import Align
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

app_version = "0.1.0-beta"

# Inicialização do Console
console = Console()

# Otimização: Obter o diretório do script uma vez para evitar chamadas repetidas.
SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))

# Configurações de tempo de atualização (em segundos)
PING_UPDATE_INTERVAL = 1.5
PUBLIC_IP_UPDATE_INTERVAL = 15
INTERFACE_UPDATE_INTERVAL = 15

# Configurações de estilo das tabelas
TABLE_STYLE = box.SIMPLE
HEADER_STYLE = "bold"
PANELY_STYLE = "bright_blue"


def is_ipv4(addr):
    """Verifica se o endereço é um IPv4 válido"""
    ipv4_pattern = r"^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$"
    return re.match(ipv4_pattern, addr) is not None


def resolve_dns(hostname):
    """Resolve um hostname para IPv4"""
    try:
        resolved_ip = socket.gethostbyname(hostname)
        return resolved_ip
    except socket.gaierror:
        return None


def create_default_config():
    """Cria um arquivo de configuração padrão com endereços Cloudflare e Google"""
    default_config = [
        {"id": 1, "addr": "1.1.1.1", "desc": "Cloudflare DNS Primary"},
        {"id": 2, "addr": "1.0.0.1", "desc": "Cloudflare DNS Secondary"},
        {"id": 3, "addr": "8.8.8.8", "desc": "Google DNS Primary"},
        {"id": 4, "addr": "8.8.4.4", "desc": "Google DNS Secondary"},
    ]
    return default_config


# Função para carregar endereços do arquivo JSON
def load_addresses():
    """Carrega a lista de endereços do arquivo icmp_monitor.json para monitoramento ICMP"""
    json_file = os.path.join(SCRIPT_DIR, "icmp_monitor.json")

    try:
        with open(json_file, "r", encoding="utf-8") as f:
            all_addresses = json.load(f)

        return all_addresses

    except FileNotFoundError:
        console.print(
            f"[yellow]Arquivo de configuração [bold]{json_file}[/bold] não encontrado![/]"
        )
        console.print(
            "[yellow]Criando arquivo de configuração padrão com endereços Cloudflare e Google...[/]"
        )

        # Criar configuração padrão
        default_addresses = create_default_config()

        # Salvar no arquivo
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(default_addresses, f, indent=2, ensure_ascii=False)

        console.print(f"[green]Arquivo [bold]{json_file}[/bold] criado com sucesso![/]")
        return default_addresses

    except json.JSONDecodeError as e:
        console.print(
            f"[red]Erro ao decodificar JSON em [bold]{json_file}[/bold]: {e}[/]"
        )
        sys.exit(1)


# Carregar endereços do arquivo JSON
addresses = load_addresses()


# Variável para armazenar o IP público
public_ip = "Carregando..."

# Variável para armazenar as interfaces de rede
network_interfaces = []

# Variáveis para estatísticas SQLite
stats_data = {}
db_lock = threading.Lock()


# =============================================================================
# CONFIGURAÇÃO E INICIALIZAÇÃO DO BANCO SQLITE
# =============================================================================
# O banco SQLite é configurado especialmente para análise instantânea:
# - WAL mode: Permite leituras concorrentes durante escritas
# - Índices otimizados: Para consultas rápidas em janelas temporais
# - Limpeza automática: Remove dados >7 dias para manter performance


def init_database():
    """
    Inicializa banco SQLite otimizado para análise temporal de conectividade.

    Configurações especiais:
    - WAL mode: Escritas não bloqueiam leituras (essencial para tempo real)
    - Cache grande: 10MB para consultas rápidas em janelas temporais
    - Sincronização normal: Balance entre performance e integridade
    """
    db_file = os.path.join(SCRIPT_DIR, "icmp_monitor.sqlite3")

    with sqlite3.connect(db_file) as conn:
        # OTIMIZAÇÕES PARA ANÁLISE EM TEMPO REAL
        conn.execute("PRAGMA journal_mode=WAL")  # Leituras concorrentes
        conn.execute("PRAGMA synchronous=NORMAL")  # Performance vs segurança
        conn.execute("PRAGMA cache_size=10000")  # 10MB cache para consultas

        # TABELA DE TARGETS - Mantém os targets de monitoramento atualizados
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ping_targets (
                target_id INTEGER PRIMARY KEY,
                ip_address TEXT NOT NULL,           -- IP ou hostname do target
                description TEXT NOT NULL,          -- Descrição do target
                tests TEXT NOT NULL,                -- Tipos de teste (JSON array)
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # TABELA DE DADOS RAW - Cada ping individual é registrado aqui
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ping_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_id INTEGER NOT NULL,         -- Referência à tabela ping_targets
                timestamp DATETIME NOT NULL,        -- Momento exato do ping
                success BOOLEAN NOT NULL,           -- True/False para conectividade
                latency REAL,                       -- Latência em milissegundos
                ttl INTEGER,                        -- Time To Live do pacote
                bytes INTEGER,                      -- Tamanho do pacote
                FOREIGN KEY (target_id) REFERENCES ping_targets(target_id)
            )
        """)

        # TABELA DE ESTATÍSTICAS - Médias e agregações calculadas
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ping_stats (
                target_id INTEGER PRIMARY KEY,
                avg_latency REAL,                   -- Média de latência
                min_latency REAL,                   -- Latência mínima
                max_latency REAL,                   -- Latência máxima
                success_rate REAL,                  -- % de sucessos
                total_results INTEGER,             -- Total de tentativas
                success_results INTEGER,           -- Total de sucessos
                fail_results INTEGER,              -- Total de falhas
                last_updated DATETIME,             -- Última atualização
                FOREIGN KEY (target_id) REFERENCES ping_targets(target_id)
            )
        """)

        # ÍNDICES OTIMIZADOS PARA JANELAS TEMPORAIS
        # Estes índices são cruciais para consultas rápidas em datetime('now', '-X minutes')
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ping_results_target_timestamp ON ping_results(target_id, timestamp)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ping_results_timestamp ON ping_results(timestamp)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ping_targets_id ON ping_targets(target_id)"
        )

        # VIEWS OTIMIZADAS PARA CONSULTAS RECORRENTES
        # Estas views simplificam as consultas de estatísticas por janela temporal

        # Forçar recriação das views com arredondamento completo
        conn.execute("DROP VIEW IF EXISTS v_stats_01min")
        conn.execute("DROP VIEW IF EXISTS v_stats_05min")
        conn.execute("DROP VIEW IF EXISTS v_stats_15min")

        # View para estatísticas de 1 minuto (com variância para detecção de anomalias)
        # TODAS as colunas numéricas são arredondadas para 2 casas decimais
        conn.execute("""
            CREATE VIEW v_stats_01min AS
            SELECT 
                pt.target_id,
                ROUND(AVG(CASE WHEN pr.success = 1 THEN pr.latency END), 2) as avg_latency,
                ROUND(MIN(CASE WHEN pr.success = 1 THEN pr.latency END), 2) as min_latency,
                ROUND(MAX(CASE WHEN pr.success = 1 THEN pr.latency END), 2) as max_latency,
                ROUND(COUNT(CASE WHEN pr.success = 1 THEN 1 END) * 100.0 / NULLIF(COUNT(pr.id), 0), 2) as success_rate,
                COUNT(pr.id) as total_results,
                COUNT(CASE WHEN pr.success = 1 THEN 1 END) as success_results,
                COUNT(CASE WHEN pr.success = 0 THEN 1 END) as fail_results,
                ROUND((
                    SELECT AVG((pr2.latency - sub.avg_latency) * (pr2.latency - sub.avg_latency))
                    FROM ping_results pr2, (
                        SELECT AVG(CASE WHEN success = 1 THEN latency END) as avg_latency
                        FROM ping_results 
                        WHERE target_id = pt.target_id AND success = 1 
                        AND julianday(timestamp) >= julianday('now', 'localtime', '-1 minute')
                    ) sub
                    WHERE pr2.target_id = pt.target_id AND pr2.success = 1 
                    AND julianday(pr2.timestamp) >= julianday('now', 'localtime', '-1 minute')
                ), 2) as variance
            FROM ping_targets pt
            LEFT JOIN ping_results pr ON pt.target_id = pr.target_id 
                AND julianday(pr.timestamp) >= julianday('now', 'localtime', '-1 minute')
            GROUP BY pt.target_id
        """)

        # View para estatísticas de 5 minutos
        # TODAS as colunas numéricas são arredondadas para 2 casas decimais
        conn.execute("""
            CREATE VIEW v_stats_05min AS
            SELECT 
                pt.target_id,
                ROUND(AVG(CASE WHEN pr.success = 1 THEN pr.latency END), 2) as avg_latency,
                ROUND(MIN(CASE WHEN pr.success = 1 THEN pr.latency END), 2) as min_latency,
                ROUND(MAX(CASE WHEN pr.success = 1 THEN pr.latency END), 2) as max_latency,
                ROUND(COUNT(CASE WHEN pr.success = 1 THEN 1 END) * 100.0 / NULLIF(COUNT(pr.id), 0), 2) as success_rate,
                COUNT(pr.id) as total_results,
                COUNT(CASE WHEN pr.success = 1 THEN 1 END) as success_results,
                COUNT(CASE WHEN pr.success = 0 THEN 1 END) as fail_results
            FROM ping_targets pt
            LEFT JOIN ping_results pr ON pt.target_id = pr.target_id 
                AND julianday(pr.timestamp) >= julianday('now', 'localtime', '-5 minutes')
            GROUP BY pt.target_id
        """)

        # View para estatísticas de 15 minutos
        # TODAS as colunas numéricas são arredondadas para 2 casas decimais
        conn.execute("""
            CREATE VIEW v_stats_15min AS
            SELECT 
                pt.target_id,
                ROUND(AVG(CASE WHEN pr.success = 1 THEN pr.latency END), 2) as avg_latency,
                ROUND(MIN(CASE WHEN pr.success = 1 THEN pr.latency END), 2) as min_latency,
                ROUND(MAX(CASE WHEN pr.success = 1 THEN pr.latency END), 2) as max_latency,
                ROUND(COUNT(CASE WHEN pr.success = 1 THEN 1 END) * 100.0 / NULLIF(COUNT(pr.id), 0), 2) as success_rate,
                COUNT(pr.id) as total_results,
                COUNT(CASE WHEN pr.success = 1 THEN 1 END) as success_results,
                COUNT(CASE WHEN pr.success = 0 THEN 1 END) as fail_results
            FROM ping_targets pt
            LEFT JOIN ping_results pr ON pt.target_id = pr.target_id 
                AND julianday(pr.timestamp) >= julianday('now', 'localtime', '-15 minutes')
            GROUP BY pt.target_id
        """)

        # Sincronizar targets do JSON com a tabela ping_targets
        sync_targets_to_database(conn)

        conn.commit()


def sync_targets_to_database(conn):
    """Sincroniza os targets do monitoring.json com a tabela ping_targets"""

    for addr in addresses:
        target_id = addr["id"]
        ip_address = addr["addr"]
        description = addr["desc"]
        tests = json.dumps(["icmp"])  # Sempre ICMP para este monitor

        # INSERT OR REPLACE para manter atualizados
        conn.execute(
            """
            INSERT OR REPLACE INTO ping_targets 
            (target_id, ip_address, description, tests, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
            (target_id, ip_address, description, tests),
        )


# Função para salvar resultado de ping no banco
def save_ping_result(target_id, success, latency=None, ttl=None, bytes_val=None):
    """Salva um resultado de ping no banco SQLite"""
    db_file = os.path.join(SCRIPT_DIR, "icmp_monitor.sqlite3")

    try:
        with db_lock:
            with sqlite3.connect(db_file) as conn:
                conn.execute(
                    """
                    INSERT INTO ping_results (target_id, timestamp, success, latency, ttl, bytes)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (target_id, datetime.now(), success, latency, ttl, bytes_val),
                )
                conn.commit()
    except Exception as e:
        console.print(f"[red]Erro ao salvar no banco: {e}[/]")


def run_ping_command(address_ip):
    """Executa o comando ping e retorna o resultado."""
    if not shutil.which("ping"):
        raise FileNotFoundError("O comando 'ping' não foi encontrado no sistema.")

    return subprocess.run(
        ["ping", "-c", "1", address_ip],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def parse_ping_output(output):
    """Analisa a saída do ping e extrai as estatísticas."""
    time_stats = "-"
    ttl_value = "-"
    bytes_value = "-"

    for line in output.splitlines():
        if "time=" in line:
            time_part = line.split("time=")[1].strip()
            try:
                time_value = float(time_part.split()[0])
                time_stats = f"{time_value:.1f}"
            except (ValueError, IndexError):
                time_stats = "-"
        if "ttl=" in line:
            ttl_value = line.split("ttl=")[1].split()[0]
        if "bytes from" in line:
            bytes_value = line.split()[0]

    return time_stats, ttl_value, bytes_value


# Função para executar o ping e retornar o resultado
def ping_address(address_obj, results):
    address_ip = address_obj["addr"]
    target_id = address_obj["id"]

    # Resolver DNS se não for IPv4
    resolved_ip = None
    if not is_ipv4(address_ip):
        resolved_ip = resolve_dns(address_ip)
        if resolved_ip is None:
            # DNS falhou, marcar como erro
            failed_timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            results[target_id] = {
                "pong": "DNS Error",
                "bytes": "-",
                "ttl": "-",
                "time": "-",
                "timestamp": f"dns_fail:{failed_timestamp}",
                "address_obj": address_obj,
                "resolved_ip": None,
            }
            time.sleep(PING_UPDATE_INTERVAL)
            return

    while True:
        try:
            result = run_ping_command(address_ip)

            if result.returncode == 0:
                response_timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                time_stats, ttl_value, bytes_value = parse_ping_output(result.stdout)

                try:
                    latency_value = float(time_stats) if time_stats != "-" else None
                    ttl_int = int(ttl_value) if ttl_value != "-" else None
                    bytes_int = int(bytes_value) if bytes_value != "-" else None
                    save_ping_result(target_id, True, latency_value, ttl_int, bytes_int)
                except (ValueError, TypeError):
                    save_ping_result(target_id, True)

                results[target_id] = {
                    "pong": "Yes",
                    "bytes": bytes_value,
                    "ttl": ttl_value,
                    "time": time_stats,
                    "timestamp": response_timestamp,
                    "address_obj": address_obj,
                    "resolved_ip": resolved_ip,
                }
            else:
                failed_timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                save_ping_result(target_id, False)

                results[target_id] = {
                    "pong": "Error",
                    "bytes": "-",
                    "ttl": "-",
                    "time": "-",
                    "timestamp": f"fail:{failed_timestamp}",
                    "address_obj": address_obj,
                    "resolved_ip": resolved_ip,
                }
            time.sleep(PING_UPDATE_INTERVAL)
        except FileNotFoundError as e:
            console.print(f"[red]Erro: {e}[/]")
            # Interrompe a thread se o ping não for encontrado
            break
        except KeyboardInterrupt:
            break


# =============================================================================
# ANÁLISE INSTANTÂNEA COM JANELAS TEMPORAIS ADAPTATIVAS
# =============================================================================
# Esta função implementa um sistema de análise instantânea que se adapta
# conforme dados são coletados:
# - 0-60s: Coleta inicial, sem estatísticas (status "Collecting...")
# - 1-5min: Análise básica com dados dos últimos 60s
# - 5-15min: Comparação entre janelas de 1min e 5min para detectar tendências
# - 15min+: Análise completa com detecção de padrões e anomalias
#
# O objetivo é fornecer insights úteis imediatamente, refinando a análise
# conforme mais dados se tornam disponíveis.


def calculate_statistics():
    """
    Calcula estatísticas adaptativas baseadas em janelas temporais curtas
    para diagnóstico instantâneo de conectividade de rede.

    Janelas analisadas:
    - 1 minuto: Detecção básica de anomalias
    - 5 minutos: Análise de estabilidade
    - 15 minutos: Classificação completa do estado
    """
    global stats_data
    db_file = os.path.join(SCRIPT_DIR, "icmp_monitor.sqlite3")

    while True:
        try:
            with db_lock:
                with sqlite3.connect(db_file) as conn:
                    # Limpar dados com mais de 7 dias para manter base pequena
                    # Usa julianday para comparação independente do formato de timestamp
                    conn.execute(
                        "DELETE FROM ping_results WHERE julianday(timestamp) < julianday('now', 'localtime', '-7 days')"
                    )

                    new_stats = {}

                    # Para cada target_id, calcular estatísticas em múltiplas janelas
                    target_ids = [addr["id"] for addr in addresses]

                    for target_id in target_ids:
                        # JANELA DE 1 MINUTO - Para detecção imediata de problemas (usando view otimizada)
                        cursor_1m = conn.execute(
                            """
                            SELECT avg_latency, success_rate, total_results, variance
                            FROM v_stats_01min WHERE target_id = ?
                        """,
                            (target_id,),
                        )

                        result_1m = cursor_1m.fetchone()

                        # JANELA DE 5 MINUTOS - Para análise de tendência (usando view otimizada)
                        cursor_5m = conn.execute(
                            """
                            SELECT avg_latency, success_rate, total_results
                            FROM v_stats_05min WHERE target_id = ?
                        """,
                            (target_id,),
                        )

                        result_5m = cursor_5m.fetchone()

                        # JANELA DE 15 MINUTOS - Para classificação completa (usando view otimizada)
                        cursor_15m = conn.execute(
                            """
                            SELECT avg_latency, success_rate, total_results
                            FROM v_stats_15min WHERE target_id = ?
                        """,
                            (target_id,),
                        )

                        result_15m = cursor_15m.fetchone()

                        # ALGORITMO DE DETECÇÃO ADAPTATIVA
                        # Escolhe a melhor janela disponível baseada na quantidade de dados
                        avg_latency, success_rate, total_results, std_dev = (
                            None,
                            0.0,
                            0,
                            0.0,
                        )
                        window_used = "collecting"

                        if (
                            result_15m and result_15m[2] >= 10
                        ):  # 15min com dados suficientes
                            avg_latency, success_rate, total_results = result_15m
                            window_used = "15min"
                        elif result_5m and result_5m[2] >= 5:  # 5min com dados mínimos
                            avg_latency, success_rate, total_results = result_5m
                            window_used = "5min"
                        elif (
                            result_1m and result_1m[2] >= 2
                        ):  # 1min com pelo menos 2 pings
                            avg_latency, success_rate, total_results = result_1m[:3]
                            # Calcular desvio padrão apenas se temos dados suficientes
                            if result_1m[3] is not None:
                                std_dev = (
                                    (result_1m[3] ** 0.5) if result_1m[3] > 0 else 0.0
                                )
                            window_used = "1min"

                        new_stats[target_id] = {
                            "avg_latency": round(avg_latency, 2)
                            if avg_latency
                            else None,
                            "success_rate": round(success_rate, 2)
                            if success_rate
                            else 0.0,
                            "total_results": total_results,
                            "std_dev": round(std_dev, 2),
                            "window_used": window_used,
                        }

                    # Atualizar variável global para uso na interface
                    stats_data = new_stats

                    # Persistir estatísticas no banco para análises futuras
                    for target_id, stats in new_stats.items():
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO ping_stats 
                            (target_id, avg_latency, success_rate, total_results, last_updated)
                            VALUES (?, ?, ?, ?, ?)
                        """,
                            (
                                target_id,
                                stats["avg_latency"],
                                stats["success_rate"],
                                stats["total_results"],
                                datetime.now(),
                            ),
                        )

                    conn.commit()

            time.sleep(15)  # Recalcular a cada 15 segundos
        except KeyboardInterrupt:
            break
        except Exception as e:
            console.print(f"[red]Erro ao calcular estatísticas: {e}[/]")
            time.sleep(15)


# Função para buscar o IP público
def fetch_public_ip():
    try:
        response = requests.get("https://api.ipify.org")
        response.raise_for_status()  # Raise an exception for HTTP errors
        return response.text.strip()
    except requests.exceptions.RequestException as e:
        console.print(f"[red]Erro ao obter IP público com requests: {e}[/]")
        return "Unknown"


# Função para atualizar o IP público a cada 60 segundos
def update_public_ip():
    global public_ip
    while True:
        try:
            public_ip = fetch_public_ip()
            time.sleep(PUBLIC_IP_UPDATE_INTERVAL)
        except KeyboardInterrupt:
            break


# Função para atualizar as interfaces de rede e seus IPv4 a cada 30 segundos
def update_network_interfaces():
    global network_interfaces
    while True:
        try:
            interfaces = []
            addrs = psutil.net_if_addrs()
            for interface_name, interface_addresses in addrs.items():
                for addr in interface_addresses:
                    if addr.family == socket.AF_INET:  # IPv4
                        interfaces.append((interface_name, addr.address))
                        break  # Pegar apenas o primeiro IPv4 por interface
            network_interfaces = interfaces
            time.sleep(INTERFACE_UPDATE_INTERVAL)
        except KeyboardInterrupt:
            break
        except Exception as e:
            console.print(f"[red]Erro ao obter interfaces de rede com psutil: {e}[/]")
            network_interfaces = [("Erro", "Erro")]
            time.sleep(INTERFACE_UPDATE_INTERVAL)


# Função para criar a tabela com os resultados de ping
def create_ping_results_table():
    def format_latency(raw, ok_style="green", err_style="dim"):
        """
        Converte o valor de latência para 'NN ms'.
        Se não for um número, devolve o raw original estilizado em vermelho.
        """
        try:
            # tenta converter para float e depois inteiro (descarta decimais)
            ms_int = int(float(raw))
            return Text(f"{ms_int} ms", style=ok_style)
        except (TypeError, ValueError):
            # qualquer falha vira texto cru com estilo de erro
            fallback = (
                raw if raw not in (None, "", "-") else "—"
            )  # travessão se vier vazio
            return Text(fallback, style=err_style)

    # Detectar tamanho do terminal para layout adaptativo
    terminal_width = console.size.width

    table = Table(title="", box=TABLE_STYLE, expand=True)

    # Layout adaptativo baseado na largura do terminal
    if terminal_width < 80:  # Terminal muito pequeno
        table.add_column(
            "Target", header_style=HEADER_STYLE, min_width=12, ratio=2, justify="right"
        )
        table.add_column("IP", header_style=HEADER_STYLE, min_width=8, ratio=1)
        table.add_column("Status", header_style=HEADER_STYLE, width=6, justify="center")
        table.add_column("ms", header_style=HEADER_STYLE, width=6, justify="right")
        # Colunas removidas: Bytes, TTL, Avg, Timestamp
    elif terminal_width < 120:  # Terminal médio
        table.add_column(
            "Target", header_style=HEADER_STYLE, min_width=15, ratio=2, justify="right"
        )
        table.add_column("Address", header_style=HEADER_STYLE, min_width=12, ratio=2)
        table.add_column("Status", header_style=HEADER_STYLE, width=6, justify="center")
        table.add_column("TTL", header_style=HEADER_STYLE, width=4, justify="right")
        table.add_column("Latency", header_style=HEADER_STYLE, width=8, justify="right")
        table.add_column("Avg", header_style=HEADER_STYLE, width=6, justify="right")
        # Colunas removidas: Bytes, Timestamp
    else:  # Terminal grande - layout completo
        table.add_column(
            "Target", header_style=HEADER_STYLE, min_width=18, ratio=2, justify="right"
        )
        table.add_column("Address", header_style=HEADER_STYLE, min_width=15, ratio=2)
        table.add_column("Pong", header_style=HEADER_STYLE, width=4, justify="center")
        table.add_column("Bytes", header_style=HEADER_STYLE, width=4, justify="right")
        table.add_column("TTL", header_style=HEADER_STYLE, width=4, justify="right")
        table.add_column(
            "Latency (ms)", header_style=HEADER_STYLE, width=5, justify="right"
        )
        table.add_column("Avg (ms)", header_style=HEADER_STYLE, width=5, justify="left")
        table.add_column("Timestamp", header_style=HEADER_STYLE, width=12)

    for target_id, result in results.items():
        # Definir a cor do texto com base no status do pong
        pong_status = result["pong"]
        if pong_status == "Waiting":
            style_pong = "dim yellow"
        elif pong_status == "Yes":
            style_pong = "green bold"
        else:
            style_pong = "dim"

        # Obter informações do endereço
        address_obj = result["address_obj"]
        target_display = address_obj["desc"]
        addr_original = address_obj["addr"]

        # Formatar exibição do endereço com IP resolvido se for hostname
        if is_ipv4(addr_original):
            ipv4_display = addr_original
        else:
            resolved_ip = result.get("resolved_ip")
            if resolved_ip:
                ipv4_display = f"{addr_original} ({resolved_ip})"
            else:
                ipv4_display = addr_original

        # TTL display without threshold coloring
        ttl_display = result["ttl"]
        ttl_text = Text(ttl_display, style="dim")

        # =================================================================
        # ALGORITMO DE DETECÇÃO DE ANOMALIAS EM TEMPO REAL
        # =================================================================
        # Esta seção implementa detecção de anomalias usando Z-score
        # para colorir o tempo atual baseado no desvio estatístico
        # em relação ao histórico recente.

        # Preparar dados para análise de anomalia
        current_latency = result["time"]
        latency_style = "dim"  # Padrão para valores não numéricos

        # Obter estatísticas da janela temporal mais adequada
        avg_time_display = "-"
        window_info = ""

        if target_id in stats_data:
            stats = stats_data[target_id]

            # Mostrar média da melhor janela disponível
            if stats["avg_latency"] is not None:
                avg_time_display = f"{stats['avg_latency']:.2f}"
                window_info = f" ({stats['window_used']})"

            # DETECÇÃO DE ANOMALIA NO TEMPO ATUAL
            # Colorir o tempo atual (não a média) baseado em análise estatística
            if (
                current_latency != "-"
                and pong_status == "Yes"
                and stats["avg_latency"] is not None
                and stats["std_dev"] > 0
            ):
                try:
                    current_ms = float(current_latency)
                    avg_ms = stats["avg_latency"]
                    std_dev = stats["std_dev"]

                    # Calcular Z-score: quantos desvios padrão o valor atual está da média
                    z_score = (current_ms - avg_ms) / std_dev

                    # CLASSIFICAÇÃO BASEADA EM DESVIO ESTATÍSTICO
                    if abs(z_score) <= 1.0:  # Dentro de 1 desvio padrão
                        latency_style = "green"  # Normal - boa performance
                    elif abs(z_score) <= 1.5:  # Entre 1 e 1.5 desvios
                        latency_style = "yellow"  # Variável - atenção
                    elif abs(z_score) <= 2.0:  # Entre 1.5 e 2 desvios
                        latency_style = "red"  # Anômalo - problema detectado
                    else:  # Mais de 2 desvios padrão
                        latency_style = "red bold"  # Crítico - investigar

                except (ValueError, ZeroDivisionError):
                    latency_style = "dim"

            elif pong_status == "Error":
                latency_style = "red bold"  # Falha de conectividade
            elif stats["window_used"] == "collecting":
                latency_style = "yellow dim"  # Ainda coletando dados

        # Formato final dos campos com estilos aplicados
        # latency_text = Text(current_latency, style=latency_style)
        # avg_text = Text(avg_time_display, style="dim")
        # remove " ms"

        latency_text = format_latency(current_latency, ok_style=latency_style)
        avg_text = format_latency(avg_time_display, ok_style=latency_style)

        # Formatar timestamp baseado no status
        timestamp_display = result["timestamp"]
        if timestamp_display.startswith("fail:"):
            # Remover prefixo "fail:" e colorir
            timestamp_text = Text(timestamp_display[5:], style="magenta bold")
        elif timestamp_display.startswith("dns_fail:"):
            # Remover prefixo "dns_fail:" e colorir
            timestamp_text = Text(timestamp_display[9:], style="red bold")
        elif timestamp_display == "-":
            timestamp_text = Text("-", style="dim")
        else:
            # Timestamp normal (resposta bem-sucedida)
            timestamp_text = Text(timestamp_display, style="green dim")

        # MONTAGEM FINAL DA LINHA DA TABELA - Layout adaptativo
        if terminal_width < 80:  # Terminal muito pequeno - 4 colunas
            table.add_row(
                target_display,
                ipv4_display,
                Text(pong_status, style=style_pong),
                latency_text,
            )
        elif terminal_width < 120:  # Terminal médio - 6 colunas
            table.add_row(
                target_display,
                ipv4_display,
                Text(pong_status, style=style_pong),
                ttl_text,
                latency_text,
                avg_text,
            )
        else:  # Terminal grande - layout completo (8 colunas)
            table.add_row(
                target_display,
                ipv4_display,
                Text(pong_status, style=style_pong),
                result["bytes"],
                ttl_text,
                latency_text,  # <- Colorido por análise estatística
                avg_text,  # <- Apenas referência (sem cor especial)
                timestamp_text,
            )

    return table


# Função para criar a tabela com as interfaces de rede
def create_network_table():
    table = Table(title="", expand=True, box=TABLE_STYLE)
    table.add_column(
        "Interface",
        justify="right",
        style="dim",
        width=30,
        no_wrap=True,
        header_style=HEADER_STYLE,
    )
    table.add_column(
        "IPv4 Address",
        justify="left",
        style="green bold",
        width=45,
        header_style=HEADER_STYLE,
    )

    for interface, ipv4 in network_interfaces:
        table.add_row(interface, ipv4)

    return table


# Função para atualizar o layout com os resultados de ping e rede
def update_layout():
    layout = Layout()

    # Adiciona uma barra de título ao layout
    layout.split_column(
        # Layout(name="header", size=1),
        Layout(name="content", ratio=1),
        Layout(name="footer", size=1),
    )

    # Divide o conteúdo principal em dois: esquerda para pings, direita para info de rede
    layout["content"].split_row(
        Layout(name="sidebar", ratio=1, minimum_size=40),
        Layout(name="main_container", ratio=3, minimum_size=40),
    )

    # Atualiza a esquerda com a tabela de pings
    layout["main_container"].update(
        Panel(
            create_ping_results_table(),
            title=Text("ICMP Tracking (PING)", style=PANELY_STYLE),
            border_style="italic",
        )
    )

    # Atualiza a direita com dois painéis: IP público e interfaces de rede
    if public_ip.lower() == "unknown":
        exibir_ip = Text("\nUnknown", style="red")
    elif public_ip.lower() == "carregando...":
        exibir_ip = Text("\nIdentificando...", style="yellow")
    else:
        exibir_ip = Text(f"\n{public_ip}", style="green bold")
    public_ip_panel = Panel(
        Align.center(exibir_ip),
        title=Text("Public IPv4 Address", style=PANELY_STYLE),
        border_style="italic",
    )
    network_table_panel = Panel(
        create_network_table(),
        title=Text("Network Interfaces", style=PANELY_STYLE),
        border_style="italic",
    )

    # constrói uma grade de 2 colunas, expansível
    info_table = Table.grid(expand=True)
    info_table.add_column(justify="right", ratio=1, style="white dim")
    info_table.add_column(justify="left", ratio=2, style="white bold")

    # adiciona a linha: desenvolvedor | versão
    info_table.add_row("Version:", Text(f" v{app_version}", style="yellow dim"))
    info_table.add_row("User:", " Herson Melo")
    info_table.add_row(
        "Current time:",
        Text(" " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"), style="yellow"),
    )

    info_panel = Panel(
        info_table,
        title=Text("Info", style=PANELY_STYLE),
        border_style="italic",
    )

    layout["sidebar"].split_column(
        Layout(info_panel, ratio=1),
        Layout(public_ip_panel, ratio=1),
        Layout(network_table_panel, ratio=4),
    )

    # # constrói uma grade de 2 colunas, expansível
    # meta = Table.grid(expand=True)
    # meta.add_column(justify="left", style="white bold")
    # meta.add_column(justify="right", style="yellow dim")

    # # adiciona a linha: desenvolvedor | versão
    # meta.add_row("User: Herson Melo", f"version {app_version}")

    # layout["header"].split_row(
    #     Layout(meta),
    # )

    # Atualiza o rodapé com uma mensagem de footer
    # footer_text = Text("[bold]'i'[/] Public IP | 'Ctrl+C' to exit.", style="dim yellow")
    footer_text = Text.assemble(
        # ("Press ", "bold"),
        # ("'i'", "bold yellow"), (" to update public ip", "dim"),
        # (" | ", "bold"),
        ("Press:", "dim"),
        ("  Ctrl+C", "bold yellow"),
        (" exit", "dim"),
    )
    layout["footer"].update(footer_text)

    # return layout

    app_layout = Layout(
        Panel(
            layout,
            title="ICMP Monitor",
            subtitle="Author: Herson Melo <hersonpc@gmail.com> © 2025 All rights reserved",
        )
    )

    return app_layout


def main():
    """ICMP Monitor - Advanced ICMP Monitoring Tool

    Executes real-time ICMP monitoring with statistical analysis
    and anomaly detection using Z-score algorithms.
    """
    # Inicializar banco SQLite
    init_database()

    # Dicionário para armazenar resultados de ping
    results = {
        address_obj["id"]: {
            "pong": "Waiting",
            "bytes": "-",
            "ttl": "-",
            "time": "-",
            "timestamp": "-",
            "address_obj": address_obj,
            "resolved_ip": None,
        }
        for address_obj in addresses
    }

    # Tornar results global para acesso das threads
    globals()["results"] = results

    # Iniciar threads para cada endereço de ping
    threads = []
    for address_obj in addresses:
        thread = threading.Thread(
            target=ping_address, args=(address_obj, results), daemon=True
        )
        thread.start()
        threads.append(thread)

    # Iniciar thread para atualizar o IP público
    public_ip_thread = threading.Thread(target=update_public_ip, daemon=True)
    public_ip_thread.start()

    # Iniciar thread para atualizar as interfaces de rede
    network_interface_thread = threading.Thread(
        target=update_network_interfaces, daemon=True
    )
    network_interface_thread.start()

    # Iniciar thread para calcular estatísticas
    stats_thread = threading.Thread(target=calculate_statistics, daemon=True)
    stats_thread.start()

    # Iniciar a exibição dinâmica com rich
    with Live(update_layout(), refresh_per_second=4, console=console) as live:
        try:
            while True:
                time.sleep(0.5)
                # Atualiza o layout na tela com o tempo atual
                live.update(update_layout())
        except KeyboardInterrupt:
            print("\n[red]Exiting...[/red]")


if __name__ == "__main__":
    main()
