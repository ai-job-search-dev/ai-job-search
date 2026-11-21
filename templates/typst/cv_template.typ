// Clean, ATS-friendly modern CV template for Typst (example document)
#set page(
  paper: "a4",
  margin: (x: 1.8cm, top: 1.8cm, bottom: 1.8cm),
)

#set text(
  font: ("Helvetica", "Arial", "Liberation Sans"),
  size: 10pt,
  fill: rgb("#222222"),
  lang: "en",
)

#import "cv_lib.typ": *
// Example usage / structural test
#cv_header(
  name: "Franco Raineri",
  title: "Software Engineer & Quantum Systems Developer",
  email: "franco.raineri@example.com",
  phone: "+54 9 11 0000 0000",
  location: "Argentina (Remote)",
  linkedin: "https://linkedin.com/in/franco-raineri",
  github: "https://github.com/franraineri"
)

#cv_section("Professional Summary")
Software Engineer specializing in modern distributed backend architectures, high-performance computing, and quantum software development (Qiskit, PennyLane). Track record of building reliable APIs, asynchronous event-driven pipelines, and mathematical simulation tooling.

#cv_section("Technical Skills")
- *Quantum Computing & Simulation:* Qiskit, PennyLane, Cirq, Quantum Algorithms (VQE, QAOA), Statevector Simulation.
- *Software Engineering & Backend:* Python (FastAPI, asyncio), TypeScript, Node.js, C++, PostgreSQL, Redis.
- *Architecture & Cloud:* Docker, CI/CD (GitHub Actions), Event-Driven Architecture, Microservices, AWS / Cloud APIs.

#cv_section("Experience")
#cv_entry(
  title: "Software Engineer",
  subtitle: "Quantum & Distributed Systems",
  location: "Remote",
  date: "2023 - Present",
  description: [
    - Designed and developed high-throughput data processing workflows and scalable API integrations.
    - Implemented simulation and algorithmic benchmark pipelines integrating hybrid quantum-classical workflows.
    - Optimized memory footprint and execution times across mathematical modeling routines.
  ]
)

#cv_section("Education")
#cv_entry(
  title: "Engineering / Computer Science Degree",
  subtitle: "University",
  location: "Argentina",
  date: "Graduated",
  description: [
    - Focus on Algorithms, Discrete Mathematics, and Software Architecture.
  ]
)
