document.addEventListener('DOMContentLoaded', function() {
    // --- Show/Hide Password Logic ---
    const togglePasswordButtons = document.querySelectorAll('.toggle-password');

    togglePasswordButtons.forEach(button => {
        button.addEventListener('click', function() {
            const passwordInput = this.previousElementSibling;
            const eyeIcon = this.querySelector('.eye-icon');
            const eyeSlashIcon = this.querySelector('.eye-slash-icon');

            if (passwordInput.type === 'password') {
                passwordInput.type = 'text';
                eyeIcon.style.display = 'none';
                eyeSlashIcon.style.display = 'block';
            } else {
                passwordInput.type = 'password';
                eyeIcon.style.display = 'block';
                eyeSlashIcon.style.display = 'none';
            }
        });
    });

    // --- Clear Input Logic ---
    const clearInputButtons = document.querySelectorAll('.clear-input');

    clearInputButtons.forEach(button => {
        const inputField = button.previousElementSibling;

        // Show/hide button based on input
        const toggleClearButton = () => {
            if (inputField.value) {
                button.style.display = 'block';
            } else {
                button.style.display = 'none';
            }
        };

        inputField.addEventListener('keyup', toggleClearButton);
        inputField.addEventListener('change', toggleClearButton);

        // Clear input on click
        button.addEventListener('click', function() {
            inputField.value = '';
            inputField.focus();
            toggleClearButton();
        });

        // Initial check
        toggleClearButton();
    });
});