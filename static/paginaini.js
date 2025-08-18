document.addEventListener("DOMContentLoaded", () => {
    // Função para animar contadores
    const animateCounter = (id, finalValue, duration = 2000) => {
        const element = document.getElementById(id);
        if (!element) return;
        let start = 0;
        const increment = Math.ceil(finalValue / (duration / 16));  
        const timer = setInterval(() => {
            start += increment;
            if (start >= finalValue) {
                element.textContent = `+${finalValue.toLocaleString('pt-BR')}`;
                clearInterval(timer);
            } else {
                element.textContent = `+${start.toLocaleString('pt-BR')}`;
            }
        }, 16);
    };

    // Inicia os contadores
    animateCounter('contador-viagens', 2500);
    animateCounter('contador-caminhoes', 180);
    animateCounter('contador-transportadoras', 60);
    animateCounter('contador-clientes', 100);

    // Lógica para o Acordeão do FAQ
    const accordionItems = document.querySelectorAll('.accordion-item');
    accordionItems.forEach(item => {
        const header = item.querySelector('.accordion-header');
        header.addEventListener('click', () => {
            const isActive = item.classList.contains('active');
            // Fecha todos os outros itens antes de abrir o novo
            accordionItems.forEach(otherItem => {
                otherItem.classList.remove('active');
            });
            // Abre o item clicado, se ele não estava ativo
            if (!isActive) {
                item.classList.add('active');
            }
        });
    });
});