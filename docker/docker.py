# \!/usr/bin/env python3
# /// script
# dependencies = ["rich"]
# ///

import re
import subprocess
import sys
import time
from datetime import datetime

from rich.console import Console
from rich.live import Live
from rich.table import Table

console = Console()


def run_command(cmd):
    """Execute a shell command and return output"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            # Se comando falhou, pode ser problema de Docker
            if "docker" in cmd and "Cannot connect to the Docker daemon" in result.stderr:
                console.print("[red]Docker não está acessível[/red]")
                console.print("[yellow]Verifique se:[/yellow]")
                console.print("• Docker está instalado e rodando")
                console.print("• Seu usuário tem permissão (grupo docker)")
                console.print("• Serviço está ativo: sudo systemctl start docker")
                sys.exit(1)
            elif "docker" in cmd and result.stderr:
                console.print(f"[red]Erro Docker: {result.stderr.strip()}[/red]")
                sys.exit(1)
        return result.stdout.strip()
    except Exception as e:
        console.print(f"[red]Erro: {e}[/red]")
        return ""


def format_date(date_str):
    """Convert Docker date format to YYYY-MM-DD HH:MM:SS"""
    try:
        # Docker format: "2025-06-24 09:55:20 -0300 -03"
        # Remove timezone part and parse
        date_part = re.sub(r" -\d{4} -\d{2}$", "", date_str.strip())
        dt = datetime.strptime(date_part, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except:
        return date_str  # Return original if parsing fails


def create_ps_table():
    """Create table for docker ps"""
    table = Table(
        title="Docker Containers", show_header=True, header_style="bold magenta"
    )
    table.add_column("CONTAINER ID", style="cyan", no_wrap=True)
    table.add_column("IMAGE", style="green")
    table.add_column("CREATED", style="yellow")
    table.add_column("STATUS", style="blue")
    table.add_column("PORTS", style="white")

    output = run_command(
        'docker ps --format "{{.ID}}|{{.Image}}|{{.CreatedAt}}|{{.Status}}|{{.Ports}}"'
    )

    for line in output.split("\n"):
        if line.strip():
            parts = line.split("|")
            if len(parts) >= 5:
                created_formatted = format_date(parts[2])
                table.add_row(parts[0], parts[1], created_formatted, parts[3], parts[4])
            elif len(parts) == 4:
                created_formatted = format_date(parts[2])
                table.add_row(parts[0], parts[1], created_formatted, parts[3], "")

    return table


def create_network_table():
    """Create table for docker networks"""
    table = Table(
        title="Docker Networks", show_header=True, header_style="bold magenta"
    )
    table.add_column("NETWORK ID", style="cyan", no_wrap=True)
    table.add_column("NAME", style="green")
    table.add_column("DRIVER", style="yellow")
    table.add_column("SCOPE", style="blue")

    output = run_command(
        'docker network ls --format "{{.ID}}|{{.Name}}|{{.Driver}}|{{.Scope}}"'
    )

    for line in output.split("\n"):
        if line.strip():
            parts = line.split("|")
            if len(parts) >= 4:
                table.add_row(parts[0], parts[1], parts[2], parts[3])

    return table


def create_images_table():
    """Create table for docker images"""
    table = Table(title="Docker Images", show_header=True, header_style="bold magenta")
    table.add_column("REPOSITORY", style="cyan")
    table.add_column("TAG", style="green")
    table.add_column("IMAGE ID", style="yellow", no_wrap=True)
    table.add_column("CREATED", style="blue")
    table.add_column("SIZE", style="white")

    output = run_command(
        'docker images --format "{{.Repository}}|{{.Tag}}|{{.ID}}|{{.CreatedAt}}|{{.Size}}"'
    )

    for line in output.split("\n"):
        if line.strip():
            parts = line.split("|")
            if len(parts) >= 5:
                created_formatted = format_date(parts[3])
                table.add_row(parts[0], parts[1], parts[2], created_formatted, parts[4])

    return table


def watch_containers():
    """Continuously monitor containers"""
    try:
        with Live(console=console, refresh_per_second=1) as live:
            while True:
                table = create_ps_table()
                live.update(table)
                time.sleep(2)
    except KeyboardInterrupt:
        console.print("\n[yellow]Monitor interrompido[/yellow]")


def check_docker():
    """Check if Docker is accessible"""
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, text=True)
        if result.returncode != 0:
            console.print("[red]Docker não está acessível[/red]")
            if "Cannot connect to the Docker daemon" in result.stderr:
                console.print("[yellow]Soluções:[/yellow]")
                console.print("• Instalar Docker: curl -fsSL https://get.docker.com | sh")
                console.print("• Iniciar serviço: sudo systemctl start docker")
                console.print("• Adicionar usuário ao grupo: sudo usermod -aG docker $USER")
                console.print("• Fazer logout/login após adicionar ao grupo")
            else:
                console.print(f"[red]{result.stderr.strip()}[/red]")
            sys.exit(1)
    except FileNotFoundError:
        console.print("[red]Docker não está instalado[/red]")
        console.print("[yellow]Instale com:[/yellow] curl -fsSL https://get.docker.com | sh")
        sys.exit(1)

def show_help():
    """Show help message"""
    console.print("[bold green]Docker Manager[/bold green]")
    console.print("")
    console.print("[cyan]Comandos:[/cyan]")
    console.print("  [bold]ps[/bold]              Lista containers")
    console.print("  [bold]net, network[/bold]    Lista redes")
    console.print("  [bold]images[/bold]          Lista imagens")
    console.print("  [bold]watch[/bold]           Monitor contínuo")
    console.print("")
    console.print("[dim]Use docker.sh para menu interativo[/dim]")


def main():
    if len(sys.argv) == 1:
        show_help()
        return

    # Verifica Docker antes de executar comandos
    check_docker()

    command = sys.argv[1].lower()

    if command == "ps":
        table = create_ps_table()
        console.print(table)
    elif command in ["net", "network"]:
        table = create_network_table()
        console.print(table)
    elif command == "images":
        table = create_images_table()
        console.print(table)
    elif command == "watch":
        console.print("[green]Monitor iniciado[/green] [dim](Ctrl+C para parar)[/dim]")
        watch_containers()
    else:
        show_help()


if __name__ == "__main__":
    main()
