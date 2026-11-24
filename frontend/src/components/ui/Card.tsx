import React from 'react';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  children: React.ReactNode;
}

export const Card: React.FC<CardProps> = ({ children, className, style, ...props }) => {
  return (
    <div
      className={cn(
        "rounded-lg overflow-hidden border",
        // UIv2: a hard-edged terminal panel with a phosphor edge and a deep drop.
        "uiv2:shadow-[inset_2px_0_0_0_rgba(198,242,74,0.4),0_20px_45px_-30px_rgba(0,0,0,0.95)]",
        // UIv3: a rust-ruled plate — the red border is the structure, not an accent.
        "uiv3:border-2 uiv3:shadow-[inset_0_1px_0_0_rgba(217,135,63,0.12),0_18px_40px_-28px_rgba(0,0,0,0.95)]",
        className
      )}
      style={{
        background: 'var(--janus-card-bg)',
        borderColor: 'var(--janus-card-border)',
        backdropFilter: 'var(--janus-card-blur)',
        WebkitBackdropFilter: 'var(--janus-card-blur)',
        ...style,
      }}
      {...props}
    >
      {children}
    </div>
  );
};

export const CardHeader: React.FC<CardProps> = ({ children, className, style, ...props }) => {
  return (
    <div
      className={cn("p-6 border-b", className)}
      style={{ borderBottomColor: 'var(--janus-card-border)', ...style }}
      {...props}
    >
      {children}
    </div>
  );
};

export const CardContent: React.FC<CardProps> = ({ children, className, ...props }) => {
  return (
    <div className={cn("p-6", className)} {...props}>
      {children}
    </div>
  );
};

export const CardFooter: React.FC<CardProps> = ({ children, className, style, ...props }) => {
  return (
    <div
      className={cn("p-6 border-t", className)}
      style={{ borderTopColor: 'var(--janus-card-border)', background: 'var(--janus-card-footer-bg)', ...style }}
      {...props}
    >
      {children}
    </div>
  );
};
