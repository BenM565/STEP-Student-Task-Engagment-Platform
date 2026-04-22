STEP – Student Task Engagement Platform

A full-stack web platform that connects university students with companies to complete real-world paid tasks. The system enables students to gain industry experience while allowing companies to efficiently clear backlog work.

Overview

STEP is designed as a marketplace platform that bridges the gap between academic learning and industry experience.

Students can access paid tasks, build verified profiles, and showcase completed work through a public portfolio. Companies can post tasks, review applicants, and track performance using structured workflows. Universities are provided with visibility into student engagement and can validate student profiles.

This project was developed as a Final Year Project at University College Cork .

Tech Stack

Backend: Flask, Flask-Login, Flask-SQLAlchemy
Frontend: HTML, Bootstrap, Jinja2
Database: MySQL
Authentication and Security: Werkzeug
Payments: Stripe escrow system
Email System: Flask-Mail with asynchronous processing

Key Features

Students can create verified accounts using their university email, apply for tasks, submit work, and build a public portfolio of completed projects . The platform provides a detailed performance breakdown, allowing students to understand how their work is evaluated .

Companies can post tasks with clear requirements, review applications, select candidates, and manage submissions. They can request revisions, approve work, and track performance metrics for students they have worked with.

Universities can monitor student engagement with industry, validate profiles, and gain insight into real-world experience gained by students.

Advanced Functionality

The platform includes a performance scoring system based on objective behavioural metrics such as on-time delivery, first-pass acceptance, quality of work, and ratings.

A secure escrow-based payment system ensures that funds are held safely and only released when work is approved.

Real-time updates are implemented using Server-Sent Events, allowing users to receive live notifications.

Lightweight AI-assisted features are used for skill extraction and task matching through heuristic-based logic .

System Architecture

The application follows a modular Flask architecture with clearly separated routes, services, and database models. A service layer is used for handling email notifications and payment processing. The system uses REST-style routing and role-based access control.

Data is stored in a MySQL relational database and managed using SQLAlchemy ORM.

Security

User passwords are securely hashed using Werkzeug. Sensitive configuration is managed through environment variables. Payment processing is handled securely through Stripe. Role-based authentication ensures that users only access permitted functionality.

Acknowledgements

This project was developed using Flask, Flask-Login, SQLAlchemy, and Werkzeug. AI tools such as ChatGPT and Claude were used to assist with development structure and problem solving.

Contact

Ben Murphy
Benmurphy565@gmail.com
