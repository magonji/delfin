function createNavbar() {
    const currentPage = window.location.pathname.split('/').pop() || 'index.html';
   
    return `
        <nav style="background: white; padding: 15px 30px; border-radius: 15px; box-shadow: 0 5px 15px rgba(0,0,0,0.1); margin-bottom: 30px; display: flex; justify-content: space-between; align-items: center;">
            <div style="display: flex; gap: 20px; align-items: center;">
                <h2 style="margin: 0; color: #667eea; font-size: 20px;">💰 Financisto Manager</h2>
                <div style="display: flex; gap: 15px;">
                    <a href="index.html" style="text-decoration: none; color: ${currentPage === 'index.html' ? '#667eea' : '#666'}; font-weight: ${currentPage === 'index.html' ? '600' : '400'}; padding: 8px 16px; border-radius: 8px; background: ${currentPage === 'index.html' ? '#f0f4ff' : 'transparent'}; transition: all 0.3s;">
                        📊 Dashboard
                    </a>
                    <a href="transactions.html" style="text-decoration: none; color: ${currentPage === 'transactions.html' ? '#667eea' : '#666'}; font-weight: ${currentPage === 'transactions.html' ? '600' : '400'}; padding: 8px 16px; border-radius: 8px; background: ${currentPage === 'transactions.html' ? '#f0f4ff' : 'transparent'}; transition: all 0.3s;">
                        💳 Transactions
                    </a>
                    <a href="loans.html" style="text-decoration: none; color: ${currentPage === 'loans.html' ? '#667eea' : '#666'}; font-weight: ${currentPage === 'loans.html' ? '600' : '400'}; padding: 8px 16px; border-radius: 8px; background: ${currentPage === 'loans.html' ? '#f0f4ff' : 'transparent'}; transition: all 0.3s;">
                        💰 Loans
                    </a>
                </div>
            </div>
        </nav>
    `;
}

// Insert navbar when page loads
document.addEventListener('DOMContentLoaded', () => {
    const container = document.querySelector('.container');
    if (container) {
        container.insertAdjacentHTML('afterbegin', createNavbar());
    }
});