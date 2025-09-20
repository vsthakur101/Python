import PyPDF2
import sys

def create_protected_pdf(input_pdf_path, output_pdf_path, user_password):
    # Open the original PDF
    try:
        with open(input_pdf_path, 'rb') as input_pdf_file:
            reader = PyPDF2.PdfReader(input_pdf_file)
            writer = PyPDF2.PdfWriter()

            # Copy all pages to the writer
            for page in reader.pages:
                writer.add_page(page)

            # Encrypt the PDF with the user password
            writer.encrypt(user_password)

            # Write the protected PDF to a new file
            with open(output_pdf_path, 'wb') as output_pdf_file:
                writer.write(output_pdf_file)
            print(f"Protected PDF created: {output_pdf_path}")

    except FileNotFoundError:
        print(f"Error: The file {input_pdf_path} was not found.")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python pdf_protection.py <input_pdf_path> <output_pdf_path> <user_password>")
        sys.exit(1)

    input_pdf_path = sys.argv[1]
    output_pdf_path = sys.argv[2]
    user_password = sys.argv[3]

    create_protected_pdf(input_pdf_path, output_pdf_path, user_password)